"""Parse user text into a WorkflowSpec (LLM first, heuristic fallback)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.graph_agent.action_schema import (
    extract_json_fields,
    fields_from_mentions,
    fields_to_schema_json,
)
from app.graph_agent.models import GraphAgentMessageRequest, GraphNodeSnapshot
from app.graph_agent.prompts import GRAPH_AGENT_SYSTEM_PROMPT
from app.graph_agent.spec import RoleDraft, StepDraft, TransitionDraft, WorkflowSpec
from app.providers.base import LLMError

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _slug(name: str, prefix: str = "step") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"{prefix}:{slug or 'item'}"


def _capture(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    value = m.group(1).strip(" .,\n\t")
    return value or None


def _int_capture(pattern: str, text: str) -> int | None:
    raw = _capture(pattern, text)
    if raw is None:
        return None
    digits = re.search(r"\d+", raw)
    return int(digits.group(0)) if digits else None


def _is_generic_target(name: str | None) -> bool:
    if not name:
        return True
    n = re.sub(r"^(to|on|in)\s+", "", name.strip(), flags=re.IGNORECASE).strip()
    if re.search(r"\b(add|one more|another role)\b", n, flags=re.IGNORECASE):
        return True
    return bool(
        re.fullmatch(
            r"(the\s+)?(actions?|connections?|graph|workflow)s?",
            n,
            flags=re.IGNORECASE,
        )
    )


def _role_names(text: str) -> list[str]:
    blob = _capture(
        r"(?:roles?|add(?: one more| another| an additional| a new)? role)\s+(.+?)(?=\s+sla|\s+action|\s+department|\s+to\s+|\s+from\s+|$)",
        text,
    )
    if not blob:
        return []
    if _is_generic_target(blob) or blob.lower().startswith("to "):
        return []
    parts = re.split(r"\s+and\s+|,(?:\s*)", blob, flags=re.IGNORECASE)
    skip = {"more", "one", "another", "additional", "new", "the", "a", "an"}
    names = []
    for part in parts:
        cleaned = part.strip(" .")
        if not cleaned or cleaned.lower() in skip:
            continue
        names.append(cleaned)
    return names


def _looks_like_remove(text: str) -> bool:
    return bool(re.search(r"\b(remove|delete|drop|clear)\b", (text or "").lower()))


def _looks_like_append_role(text: str) -> bool:
    lower = (text or "").lower()
    return bool(
        re.search(
            r"add (one more |another |an additional |a new |now )?roles?(?:\s|$)|"
            r"one more role|another role|additional role|"
            r"add a role|add the role|"
            r"add roles? (in|to|on|into)",
            lower,
        )
    )


def _looks_like_update_form(text: str) -> bool:
    lower = (text or "").lower()
    if _looks_like_append_role(text) or _looks_like_remove(text):
        return False
    return bool(
        re.search(
            r"custom action|action form|form field|additional field|"
            r"custom field|action ui|open form builder|"
            r"add (a |the )?custom actions?|custom actions? in (the )?role",
            lower,
        )
    )


def _looks_like_update_role(text: str) -> bool:
    lower = (text or "").lower()
    if (
        _looks_like_append_role(text)
        or _looks_like_update_form(text)
        or _looks_like_remove(text)
    ):
        return False
    return bool(
        re.search(
            r"\b(update|edit|change|modify|set)\b.+\broles?\b|"
            r"\broles?.+\bsla\b|"
            r"\bsla\b.+\broles?\b",
            lower,
        )
    ) and not re.search(r"\b(card|status card|status name)\b", lower)


def _looks_like_update_step(text: str) -> bool:
    lower = (text or "").lower()
    if (
        _looks_like_append_role(text)
        or _looks_like_update_form(text)
        or _looks_like_update_role(text)
        or _looks_like_remove(text)
    ):
        return False
    if not re.search(r"\b(update|edit|change|modify|rename|set)\b", lower):
        return False
    if re.search(r"\b(card|status|step|phase)\b", lower):
        return True
    return bool(re.search(r"\bsla\b", lower) and not re.search(r"\broles?\b", lower))


def _is_placeholder_card_name(name: str | None) -> bool:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    return n in {
        "new",
        "a new",
        "the new",
        "status",
        "card",
        "step",
        "status card",
        "new status",
        "new card",
        "new step",
        "new status card",
        "a new status",
        "a new card",
        "a new status card",
        "a status card",
        "the status card",
        "existing status",
    }


def _looks_like_create_step(text: str) -> bool:
    lower = (text or "").lower()
    if (
        _looks_like_append_role(text)
        or _looks_like_update_form(text)
        or _looks_like_update_role(text)
        or _looks_like_update_step(text)
        or _looks_like_remove(text)
    ):
        return False
    return bool(
        re.search(
            r"\b(add|create|make)\b.+\b(status\s*card|status|card|step)\b|"
            r"\bnew\s+(status\s*card|status|card|step)\b",
            lower,
        )
    )


def _looks_like_delete_form(text: str) -> bool:
    if not _looks_like_remove(text):
        return False
    return bool(
        re.search(
            r"custom action|action form|form field|additional field|"
            r"custom field|action ui",
            (text or "").lower(),
        )
    )


def _looks_like_delete_role(text: str) -> bool:
    if not _looks_like_remove(text) or _looks_like_delete_form(text):
        return False
    return bool(re.search(r"\broles?\b", (text or "").lower()))


def _looks_like_delete_step(text: str) -> bool:
    if (
        not _looks_like_remove(text)
        or _looks_like_delete_form(text)
        or _looks_like_delete_role(text)
    ):
        return False
    return bool(
        re.search(
            r"\b(status\s*card|card|status|step)\b",
            (text or "").lower(),
        )
    ) and not re.search(r"\b(action|connection|edge)\b", (text or "").lower())


def _looks_like_delete_transition(text: str) -> bool:
    if (
        not _looks_like_remove(text)
        or _looks_like_delete_form(text)
        or _looks_like_delete_role(text)
        or _looks_like_delete_step(text)
    ):
        return False
    return bool(
        re.search(r"\b(action|connection|edge|link)\b", (text or "").lower())
    )


def heuristic_parse(text: str, nodes: list[GraphNodeSnapshot]) -> WorkflowSpec:
    """Best-effort parse when the LLM is unavailable."""
    spec = WorkflowSpec()
    lower = text.lower()
    update_form = _looks_like_update_form(text)
    update_roles = _looks_like_update_role(text) and not update_form
    update_step = _looks_like_update_step(text)
    append_roles = _looks_like_append_role(text) and not update_form
    wants_connect = (
        bool(re.search(r"\bconnect|from .+ to |\blink\b", lower))
        or append_roles
        or update_form
        or update_roles
    )

    if _looks_like_delete_form(text):
        spec.transitions.append(
            TransitionDraft(
                key="edge:0",
                delete_form=True,
                update_form=False,
                append_roles=False,
                roles=[],
            )
        )
        return spec

    if _looks_like_delete_role(text):
        names = _role_names(text)
        spec.transitions.append(
            TransitionDraft(
                key="edge:0",
                delete_role=True,
                append_roles=False,
                roles=[RoleDraft(role_name=names[0])] if names else [],
            )
        )
        return spec

    if _looks_like_delete_step(text):
        name_ref = (
            _capture(
                r"(?:card|status|step)\s+(.+?)(?:\s+sla|\s+phase|$)",
                text,
            )
            or _capture(
                r"(?:remove|delete|drop)\s+(.+?)(?:\s+card|\s+status|\s+step|$)",
                text,
            )
        )
        if name_ref and (
            _is_generic_target(name_ref) or _is_placeholder_card_name(name_ref)
        ):
            name_ref = None
        spec.steps.append(
            StepDraft(
                key="delete:0",
                name=name_ref or "Existing status",
                name_ref=name_ref,
                delete=True,
                update=True,
            )
        )
        return spec

    if _looks_like_delete_transition(text):
        spec.transitions.append(
            TransitionDraft(
                key="edge:0",
                delete_transition=True,
                append_roles=False,
                roles=[],
            )
        )
        return spec

    if update_step:
        name_ref = (
            _capture(
                r"(?:card|status|step)\s+(.+?)(?:\s+sla|\s+phase|\s+to\s+\d|\s+officer|\s+citizen|$)",
                text,
            )
            or _capture(r"(?:update|edit|change|rename)\s+(.+?)(?:\s+sla|\s+to\s+|\s+phase|$)", text)
        )
        if name_ref and _is_generic_target(name_ref):
            name_ref = None
        new_name = _capture(r"(?:rename(?:d)? (?:to|as)|new name(?: as)?)\s+([^,\n]+)", text)
        spec.steps.append(
            StepDraft(
                key="update:0",
                name=new_name or name_ref or "Existing status",
                name_ref=name_ref,
                update=True,
                sla_hours=_int_capture(r"sla(?: hours)?(?: (?:will be|to|as))?\s+(\d+)", text)
                or _int_capture(r"to\s+(\d+)\s*(?:hours?)?", text),
                phase_name=_capture(
                    r"phase(?: as| to)?\s+([^,\n]+?)(?:,| officer| citizen| sla| description|$)",
                    text,
                ),
                officer_status_name=_capture(
                    r"officer status(?: as| to)?\s+([^,\n]+?)(?:,| citizen| sla| description| phase|$)",
                    text,
                ),
                citizen_status_name=_capture(
                    r"citizen status(?: as| to)?\s+([^,\n]+?)(?:,| sla| description| officer| phase|$)",
                    text,
                ),
                description=_capture(r"description(?: will be| to)?\s+(.+)$", text),
            )
        )
        return spec

    card_chunks: list[str] = []
    if re.search(r"\b(add|create|new)\b.+\b(card|status|step)\b", lower) or "status name" in lower:
        # Split additional cards listed after "cards" / "and"
        named = re.findall(
            r"(?:status name(?: as)?|card(?: named)?|named)\s+([^,\n]+?)(?:,| phase| officer| citizen| sla| description|$)",
            text,
            flags=re.IGNORECASE,
        )
        if named:
            card_chunks = named
        else:
            # "two cards X and Y"
            m = re.search(
                r"cards?\s+(.+?)(?:\s+and connect|\s+connect|$)",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                parts = re.split(r"\s+and\s+", m.group(1), flags=re.IGNORECASE)
                card_chunks = [p.strip(" .") for p in parts if p.strip()]

        card_chunks = [
            c.strip()
            for c in card_chunks
            if c.strip() and not _is_placeholder_card_name(c)
        ]

    if not card_chunks and not wants_connect:
        # Treat the whole utterance as one card if they gave a status name-like phrase
        if "phase" in lower or "sla" in lower:
            card_chunks = [_capture(r"(?:status name(?: as)?|named)\s+([^,\n]+)", text) or "New status"]
        elif _looks_like_create_step(text):
            card_chunks = ["New status"]

    empty_graph = len(nodes) == 0
    for i, chunk in enumerate(card_chunks):
        name = chunk.strip()
        # For the first "full" card, pull fields from the whole text
        source = text if i == 0 and ("phase" in lower or "sla" in lower) else chunk
        spec.steps.append(
            StepDraft(
                key=_slug(name),
                name=name,
                phase_name=_capture(r"phase(?: as)?\s+([^,\n]+?)(?:,| officer| citizen| sla| description|$)", source),
                officer_status_name=_capture(
                    r"officer status(?: as)?\s+([^,\n]+?)(?:,| citizen| sla| description| phase|$)",
                    source,
                ),
                citizen_status_name=_capture(
                    r"citizen status(?: as)?\s+([^,\n]+?)(?:,| sla| description| officer| phase|$)",
                    source,
                ),
                sla_hours=_int_capture(r"sla(?: hours)?(?: will be)?\s+(\d+)", source),
                description=_capture(r"description(?: will be)?\s+(.+)$", source),
                is_initial=True if empty_graph and i == 0 else False,
            )
        )

    if wants_connect:
        from_ref = _capture(r"(?:from|connect(?: both)?)\s+(.+?)\s+to\s+", text)
        if not from_ref:
            from_ref = _capture(r"connect .+ with the (.+?) card", text)
        if from_ref and (append_roles or update_form or update_roles) and _is_generic_target(from_ref):
            from_ref = None
        if not from_ref and nodes and not append_roles and not update_form and not update_roles:
            from_ref = nodes[0].name

        to_names: list[str] = []
        dest = _capture(
            r"(?:from\s+.+?|connect(?: both)?\s+.+?)\s+to\s+(.+?)(?:\s+with action|\s+action|\s+role|$)",
            text,
        )
        if dest and not _is_generic_target(dest):
            to_names = [p.strip() for p in re.split(r"\s+and\s+", dest) if p.strip()]
        if not append_roles and not update_form and not update_roles:
            to_blob = _capture(r"\bto\s+(.+?)(?:\s+with action|\s+action|$)", text)
            if to_blob and not _is_generic_target(to_blob) and not to_names:
                to_names = [p.strip() for p in re.split(r"\s+and\s+", to_blob) if p.strip()]
        if not to_names and spec.steps:
            to_names = [s.name for s in spec.steps]

        action_name = _capture(
            r"(?:on|in)\s+(?:the\s+)?(.+?)\s+action",
            text,
        ) or _capture(
            r"action(?: as)?\s+([^,\n]+?)(?:,| role| department|$)", text
        )
        if action_name and _is_generic_target(action_name):
            action_name = None

        if update_form:
            spec.transitions.append(
                TransitionDraft(
                    key="edge:0",
                    from_ref=from_ref,
                    to_ref=to_names[0] if to_names else None,
                    action_name=action_name,
                    update_form=True,
                    append_roles=False,
                    form_schema=_schema_from_user_text(text),
                    roles=[],
                )
            )
            return spec

        if update_roles:
            spec.transitions.append(
                TransitionDraft(
                    key="edge:0",
                    from_ref=from_ref,
                    to_ref=to_names[0] if to_names else None,
                    action_name=action_name,
                    update_roles=True,
                    append_roles=False,
                    update_sla_hours=_int_capture(
                        r"sla(?: hours)?(?: (?:will be|to|as))?\s+(\d+)", text
                    )
                    or _int_capture(r"to\s+(\d+)\s*(?:hours?)?", text),
                    roles=[],
                )
            )
            return spec

        dept_name = _capture(r"department(?: as)?\s+([^,\n]+?)(?:,| action| role|$)", text)
        role_sla = _int_capture(r"(?:role )?sla(?: hours)?(?: will be)?\s+(\d+)", text)
        role_names = _role_names(text)
        skip_role_words = {
            "to the actions",
            "to the action",
            "to actions",
            "the actions",
            "the action",
        }
        role_names = [
            n
            for n in role_names
            if n and n.strip().lower() not in skip_role_words and not _is_generic_target(n)
        ]
        if not role_names:
            role_names = [None]

        targets = to_names if to_names else ([None] if append_roles else ["<unknown>"])
        for i, to_name in enumerate(targets):
            to_key = None
            if to_name:
                for step in spec.steps:
                    if _slug(step.name) == _slug(to_name) or step.name.lower() == to_name.lower():
                        to_key = step.key
                        break
            spec.transitions.append(
                TransitionDraft(
                    key=f"edge:{i}",
                    from_ref=from_ref,
                    to_ref=to_key or to_name,
                    to_temp_key=to_key,
                    action_name=action_name,
                    department_name=dept_name,
                    append_roles=append_roles,
                    roles=[
                        RoleDraft(
                            role_name=name,
                            sla_hours=role_sla,
                            action_ui_schema=_schema_from_user_text(text),
                        )
                        for name in role_names
                    ],
                )
            )

    return spec


def _schema_from_user_text(text: str) -> str | None:
    pasted = extract_json_fields(text)
    if pasted:
        return fields_to_schema_json(pasted)
    return fields_to_schema_json(fields_from_mentions(text))


def _role_from_llm(raw: dict[str, Any]) -> RoleDraft:
    schema = fields_to_schema_json(
        raw.get("form_fields")
        or raw.get("action_ui_schema")
        or raw.get("ActionUiSchema")
    )
    return RoleDraft(
        role_name=raw.get("role_name"),
        sla_hours=raw.get("sla_hours"),
        action_ui_schema=schema,
    )


def _fill_missing_form_fields(spec: WorkflowSpec, text: str) -> None:
    schema = _schema_from_user_text(text)
    if not schema:
        return
    for edge in spec.transitions:
        if edge.update_form and not edge.form_schema:
            edge.form_schema = schema
        for role in edge.roles:
            if not role.existing and not role.action_ui_schema:
                role.action_ui_schema = schema


def _coerce_update_form(spec: WorkflowSpec, text: str) -> None:
    if not _looks_like_update_form(text):
        return
    if not spec.transitions:
        fallback = heuristic_parse(text, [])
        spec.transitions.extend(fallback.transitions)
        return
    schema = _schema_from_user_text(text)
    for edge in spec.transitions:
        edge.update_form = True
        edge.append_roles = False
        if schema and not edge.form_schema:
            edge.form_schema = schema
        named = [r for r in edge.roles if r.role_name or r.role_id]
        if named:
            if schema:
                for r in named:
                    if not r.action_ui_schema:
                        r.action_ui_schema = schema
            edge.roles = named
        else:
            edge.roles = []


def _coerce_create_step(spec: WorkflowSpec, text: str, nodes: list[GraphNodeSnapshot]) -> None:
    if spec.steps or spec.transitions or not _looks_like_create_step(text):
        return
    fallback = heuristic_parse(text, nodes)
    spec.steps.extend(fallback.steps)


def _coerce_existing_edits(spec: WorkflowSpec, text: str, nodes: list[GraphNodeSnapshot]) -> None:
    if (
        _looks_like_delete_form(text)
        or _looks_like_delete_role(text)
        or _looks_like_delete_step(text)
        or _looks_like_delete_transition(text)
    ):
        flagged = any(s.delete for s in spec.steps) or any(
            e.delete_form or e.delete_role or e.delete_transition
            for e in spec.transitions
        )
        if not flagged:
            fallback = heuristic_parse(text, nodes)
            spec.steps.extend(fallback.steps)
            spec.transitions.extend(fallback.transitions)
        return
    if _looks_like_update_form(text):
        _coerce_update_form(spec, text)
        return
    if _looks_like_update_role(text):
        if not spec.transitions:
            spec.transitions.extend(heuristic_parse(text, nodes).transitions)
        else:
            sla = _int_capture(r"sla(?: hours)?(?: (?:will be|to|as))?\s+(\d+)", text)
            for edge in spec.transitions:
                edge.update_roles = True
                edge.append_roles = False
                edge.update_form = False
                if sla is not None:
                    edge.update_sla_hours = sla
                edge.roles = [r for r in edge.roles if r.role_name or r.role_id]
        return
    if _looks_like_update_step(text):
        if not spec.steps:
            spec.steps.extend(heuristic_parse(text, nodes).steps)
        else:
            for step in spec.steps:
                step.update = True
                if not step.name_ref:
                    step.name_ref = step.name


def spec_from_llm_json(data: dict[str, Any], nodes: list[GraphNodeSnapshot]) -> WorkflowSpec:
    spec = WorkflowSpec(raw=data)
    empty_graph = len(nodes) == 0
    for i, raw in enumerate(data.get("update_steps") or []):
        name_ref = str(raw.get("name_ref") or raw.get("name") or "").strip() or None
        new_name = str(raw.get("name") or "").strip() or name_ref or "Existing status"
        spec.steps.append(
            StepDraft(
                key=str(raw.get("key") or f"update:{i}"),
                name=new_name,
                name_ref=name_ref,
                update=True,
                phase_name=raw.get("phase_name"),
                officer_status_name=raw.get("officer_status_name"),
                citizen_status_name=raw.get("citizen_status_name"),
                sla_hours=raw.get("sla_hours"),
                description=raw.get("description"),
            )
        )
    for i, raw in enumerate(data.get("create_steps") or []):
        name = str(raw.get("name") or "").strip() or "New status"
        spec.steps.append(
            StepDraft(
                key=str(raw.get("key") or _slug(name)),
                name=name,
                phase_name=raw.get("phase_name"),
                officer_status_name=raw.get("officer_status_name"),
                citizen_status_name=raw.get("citizen_status_name"),
                sla_hours=raw.get("sla_hours"),
                description=raw.get("description"),
                is_final=raw.get("is_final") if raw.get("is_final") is not None else None,
                is_initial=raw.get("is_initial") if raw.get("is_initial") is not None else (empty_graph and i == 0),
            )
        )
    for i, raw in enumerate(data.get("delete_steps") or []):
        name_ref = str(raw.get("name_ref") or raw.get("name") or "").strip() or None
        spec.steps.append(
            StepDraft(
                key=str(raw.get("key") or f"delete:{i}"),
                name=name_ref or "Existing status",
                name_ref=name_ref,
                delete=True,
                update=True,
            )
        )
    for i, raw in enumerate(data.get("create_transitions") or []):
        deleting = bool(
            raw.get("delete_role")
            or raw.get("delete_form")
            or raw.get("delete_transition")
        )
        roles_raw = raw.get("roles") or ([] if deleting else [{}])
        spec.transitions.append(
            TransitionDraft(
                key=str(raw.get("key") or f"edge:{i}"),
                from_ref=raw.get("from_ref"),
                to_ref=raw.get("to_ref"),
                action_name=raw.get("action_name"),
                department_name=raw.get("department_name"),
                append_roles=bool(raw.get("append_roles")) and not deleting,
                update_form=bool(raw.get("update_form")) and not deleting,
                update_roles=bool(raw.get("update_roles")) and not deleting,
                delete_role=bool(raw.get("delete_role")),
                delete_form=bool(raw.get("delete_form")),
                delete_transition=bool(raw.get("delete_transition")),
                update_sla_hours=raw.get("update_sla_hours")
                or (
                    (raw.get("roles") or [{}])[0].get("sla_hours")
                    if raw.get("update_roles")
                    else None
                ),
                form_schema=fields_to_schema_json(
                    raw.get("form_fields")
                    or (
                        roles_raw[0].get("form_fields")
                        if roles_raw and isinstance(roles_raw[0], dict)
                        else None
                    )
                ),
                roles=[_role_from_llm(r) for r in roles_raw if isinstance(r, dict)],
            )
        )
    return spec


def _loads_json(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    fence = _FENCE.search(s)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (TypeError, ValueError):
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(s[start : end + 1])
                return obj if isinstance(obj, dict) else None
            except (TypeError, ValueError):
                return None
    return None


def graph_context_prompt(req: GraphAgentMessageRequest) -> str:
    nodes = [
        {
            "stepId": n.step_id,
            "name": n.name,
            "slaHours": n.sla_hours,
            "phaseId": n.phase_id,
            "statusId": n.status_id,
            "citizenStatusId": n.citizen_status_id,
            "isInitial": n.is_initial,
        }
        for n in req.graph.nodes
    ]
    edges = [
        {
            "fromStepId": e.from_step_id,
            "toStepId": e.to_step_id,
            "actionName": e.action_name,
            "roles": [
                {
                    "roleName": r.role_name,
                    "hasActionForm": bool(r.action_ui_schema),
                }
                for r in e.roles
            ],
        }
        for e in req.graph.edges
    ]
    catalog_lines = []
    if req.catalogs.phases:
        catalog_lines.append("- Phases: " + ", ".join(f'"{p.label}"' for p in req.catalogs.phases[:40]))
    if req.catalogs.statuses:
        catalog_lines.append("- Statuses: " + ", ".join(f'"{s.label}"' for s in req.catalogs.statuses[:60]))
    if req.catalogs.actions:
        catalog_lines.append("- Actions: " + ", ".join(f'"{a.label}"' for a in req.catalogs.actions[:60]))
    if req.catalogs.roles:
        catalog_lines.append("- Roles: " + ", ".join(f'"{r.label}"' for r in req.catalogs.roles[:80]))
    catalogs_text = ""
    if catalog_lines:
        catalogs_text = "Master Catalogs in Portal (prefer these exact names for action_name, role_name, phase_name, officer_status_name):\n" + "\n".join(catalog_lines) + "\n\n"

    return (
        f"WorkflowId: {req.workflow_id}\n"
        f"WorkflowTypeId: {req.workflow_type_id}\n"
        f"DepartmentId: {req.department_id}\n"
        f"Existing cards on canvas: {json.dumps(nodes)}\n"
        f"Existing connections on canvas: {json.dumps(edges)}\n\n"
        f"{catalogs_text}"
        f"User request:\n{req.text}\n"
    )


async def parse_message(req: GraphAgentMessageRequest) -> WorkflowSpec:
    try:
        from app.providers.factory import get_provider

        provider = get_provider()
        raw = await provider.generate(GRAPH_AGENT_SYSTEM_PROMPT, graph_context_prompt(req))
        data = _loads_json(raw)
        if data:
            spec = spec_from_llm_json(data, req.graph.nodes)
            if not spec.steps and not spec.transitions:
                fallback = heuristic_parse(req.text, req.graph.nodes)
                if fallback.steps or fallback.transitions:
                    logger.info("LLM returned empty spec; using heuristic parse")
                    spec = fallback
            _coerce_create_step(spec, req.text, req.graph.nodes)
            _coerce_existing_edits(spec, req.text, req.graph.nodes)
            _fill_missing_form_fields(spec, req.text)
            return spec
        logger.warning("LLM returned non-JSON; using heuristic parse")
    except LLMError as exc:
        logger.warning("LLM unavailable (%s); using heuristic parse", exc)
    except Exception:
        logger.exception("LLM parse failed; using heuristic parse")
    return heuristic_parse(req.text, req.graph.nodes)
