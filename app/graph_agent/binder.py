"""Fill catalog ids on the spec or emit dropdown questions."""

from __future__ import annotations

from app.graph_agent.action_schema import schema_from_user_spec
from app.graph_agent.models import (
    CatalogsPayload,
    GraphAgentMessageRequest,
    GraphSnapshot,
    Question,
    QuestionOption,
)
from app.graph_agent.resolve import _score, match_catalog, match_node, question_from_match
from app.graph_agent.spec import RoleDraft, TransitionDraft, WorkflowSpec


def _is_edge_edit(edge: TransitionDraft) -> bool:
    return bool(
        edge.append_roles
        or edge.update_form
        or edge.update_roles
        or edge.delete_role
        or edge.delete_form
        or edge.delete_transition
    )


def _node_name(step_id: int | None, nodes) -> str | None:
    if step_id is None:
        return None
    for n in nodes:
        if n.step_id == step_id:
            return n.name
    return None


def _connection_title(edge: TransitionDraft, spec: WorkflowSpec, nodes) -> str:
    step_by_key = {s.key: s for s in spec.steps}
    from_name = None
    if edge.from_temp_key and edge.from_temp_key in step_by_key:
        from_name = step_by_key[edge.from_temp_key].name
    if not from_name:
        from_name = _node_name(edge.from_step_id, nodes) or edge.from_ref or "Source card"
    to_name = None
    if edge.to_temp_key and edge.to_temp_key in step_by_key:
        to_name = step_by_key[edge.to_temp_key].name
    if not to_name:
        to_name = _node_name(edge.to_step_id, nodes) or edge.to_ref or "Target card"
    return f"{from_name}  →  {to_name}"


def _int_val(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def apply_answers(
    spec: WorkflowSpec,
    answers: dict[str, int],
    catalogs: CatalogsPayload,
    graph: GraphSnapshot | None = None,
    text_answers: dict[str, str] | None = None,
) -> None:
    by_id_phase = {i.id: i for i in catalogs.phases}
    by_id_status = {i.id: i for i in catalogs.statuses}
    by_id_action = {i.id: i for i in catalogs.actions}
    by_id_role = {i.id: i for i in catalogs.roles}
    by_id_dept = {i.id: i for i in catalogs.departments}
    texts = text_answers or {}

    for step in spec.steps:
        pid = answers.get(f"{step.key}:phase")
        if pid:
            step.phase_id = pid
            step.phase_confirmed = True
            if pid in by_id_phase:
                step.phase_name = by_id_phase[pid].label
        oid = answers.get(f"{step.key}:officer_status")
        if oid:
            step.officer_status_id = oid
            step.officer_status_confirmed = True
            if oid in by_id_status:
                step.officer_status_name = by_id_status[oid].label
        cid = answers.get(f"{step.key}:citizen_status")
        if cid:
            step.citizen_status_id = cid
            step.citizen_status_confirmed = True
            if cid in by_id_status:
                step.citizen_status_name = by_id_status[cid].label
        sla = _int_val(answers.get(f"{step.key}:sla_hours")) if f"{step.key}:sla_hours" in answers else _int_val(texts.get(f"{step.key}:sla_hours"))
        if sla is not None:
            step.sla_hours = sla
            step.sla_confirmed = True
        final_flag = answers.get(f"{step.key}:is_final")
        if final_flag is not None:
            step.is_final = bool(final_flag)
            step.is_final_confirmed = True
        stid = answers.get(f"{step.key}:step_type")
        if stid:
            step.step_type_id = stid
            step.step_type_confirmed = True
            for item in catalogs.step_types:
                if item.id == stid:
                    step.step_type_name = item.label
                    break
        sid = answers.get(f"{step.key}:existing_step")
        if sid:
            step.existing_step_id = sid
            step.update = True
        name_text = texts.get(f"{step.key}:name")
        if name_text and str(name_text).strip():
            step.name = str(name_text).strip()
        desc = texts.get(f"{step.key}:description")
        if desc and str(desc).strip():
            step.description = str(desc).strip()
            step.description_confirmed = True

    for edge in spec.transitions:
        aid = answers.get(f"{edge.key}:action")
        if aid and aid in by_id_action:
            edge.action_id = aid
            edge.action_confirmed = True
            item = by_id_action[aid]
            edge.action_name = item.label
            extra = item.extra or {}
            edge.action_type_id = extra.get("actionTypeId")
            edge.is_instant = bool(extra.get("isInstantAction"))
        did = answers.get(f"{edge.key}:department")
        if did and did in by_id_dept:
            edge.department_id = did
            edge.department_confirmed = True
            edge.department_name = by_id_dept[did].label
        fid = answers.get(f"{edge.key}:from")
        if fid:
            edge.from_step_id = fid
        elif f"{edge.key}:from" in texts:
            val = texts[f"{edge.key}:from"].strip()
            if val in step_by_key:
                edge.from_temp_key = val
            elif val.lower() in step_by_name:
                edge.from_temp_key = step_by_name[val.lower()].key
        tid = answers.get(f"{edge.key}:to")
        if tid:
            edge.to_step_id = tid
        elif f"{edge.key}:to" in texts:
            val = texts[f"{edge.key}:to"].strip()
            if val in step_by_key:
                edge.to_temp_key = val
            elif val.lower() in step_by_name:
                edge.to_temp_key = step_by_name[val.lower()].key
        edge_idx = answers.get(f"{edge.key}:existing_edge")
        if edge_idx is not None and graph and 0 <= edge_idx < len(graph.edges):
            ge = graph.edges[edge_idx]
            edge.from_step_id = ge.from_step_id
            edge.to_step_id = ge.to_step_id
            if ge.action_id:
                edge.action_id = ge.action_id
            if ge.action_name:
                edge.action_name = ge.action_name
            if ge.action_type_id:
                edge.action_type_id = ge.action_type_id
            if ge.is_instant is not None:
                edge.is_instant = bool(ge.is_instant)
            if ge.department_id:
                edge.department_id = ge.department_id
            if ge.step_action_id:
                edge.step_action_id = ge.step_action_id
        for i, role in enumerate(edge.roles or [None]):
            rid = answers.get(f"{edge.key}:role:{i}")
            if rid and rid in by_id_role:
                if role is None:
                    role = RoleDraft()
                    if not edge.roles:
                        edge.roles = [role]
                    else:
                        edge.roles[i] = role
                role.role_id = rid
                role.role_confirmed = True
                item = by_id_role[rid]
                role.role_name = item.label
                extra = item.extra or {}
                role.role_meta = extra.get("roleMeta")
            sla = _int_val(answers.get(f"{edge.key}:role:{i}:sla")) if f"{edge.key}:role:{i}:sla" in answers else _int_val(texts.get(f"{edge.key}:role:{i}:sla"))
            if sla is not None and role is not None:
                role.sla_hours = sla
                role.sla_confirmed = True
        target = answers.get(f"{edge.key}:existing_role")
        if target:
            edge.target_role_id = target
        sla_upd = _int_val(answers.get(f"{edge.key}:role:0:sla")) if f"{edge.key}:role:0:sla" in answers else _int_val(texts.get(f"{edge.key}:role:0:sla"))
        if sla_upd is not None:
            edge.update_sla_hours = sla_upd
            if edge.roles:
                edge.roles[0].sla_hours = sla_upd
                edge.roles[0].sla_confirmed = True
        blob = texts.get(f"{edge.key}:form_fields")
        if blob:
            schema = schema_from_user_spec(blob)
            if schema:
                edge.form_schema = schema
            else:
                edge.form_schema = None
                edge.form_fields_nl = blob


def _merge_existing_roles(spec: WorkflowSpec, req: GraphAgentMessageRequest) -> None:
    """Keep roles already on the graph when the user adds another role."""
    for edge in spec.transitions:
        if not (
            edge.append_roles
            or edge.update_form
            or edge.update_roles
            or edge.delete_role
            or edge.delete_form
            or edge.delete_transition
        ):
            continue
        matched = None
        for ge in req.graph.edges:
            same_ends = (
                (edge.from_step_id and ge.from_step_id == edge.from_step_id)
                or (
                    edge.from_ref
                    and _node_name(ge.from_step_id, req.graph.nodes)
                    and edge.from_ref.lower() in (_node_name(ge.from_step_id, req.graph.nodes) or "").lower()
                )
            ) and (
                (edge.to_step_id and ge.to_step_id == edge.to_step_id)
                or (
                    edge.to_ref
                    and _node_name(ge.to_step_id, req.graph.nodes)
                    and (edge.to_ref or "").lower()
                    in (_node_name(ge.to_step_id, req.graph.nodes) or "").lower()
                )
            )
            if same_ends:
                matched = ge
                break
        if matched is None and (edge.action_name or edge.to_ref or edge.from_ref):
            needle = (edge.action_name or edge.to_ref or edge.from_ref or "").lower()
            for ge in req.graph.edges:
                action = (ge.action_name or "").lower()
                to_name = (_node_name(ge.to_step_id, req.graph.nodes) or "").lower()
                from_name = (_node_name(ge.from_step_id, req.graph.nodes) or "").lower()
                if needle and (
                    needle in action
                    or action in needle
                    or needle in to_name
                    or needle in from_name
                ):
                    matched = ge
                    break
        if matched is None and len(req.graph.edges) == 1:
            matched = req.graph.edges[0]
        if matched is None:
            continue
        edge.from_step_id = matched.from_step_id
        edge.to_step_id = matched.to_step_id
        if matched.action_id:
            edge.action_id = matched.action_id
        if matched.action_name:
            edge.action_name = matched.action_name
        if matched.action_type_id:
            edge.action_type_id = matched.action_type_id
        if matched.is_instant is not None:
            edge.is_instant = bool(matched.is_instant)
        if matched.department_id:
            edge.department_id = matched.department_id
        if matched.step_action_id:
            edge.step_action_id = matched.step_action_id
        existing: list[RoleDraft] = []
        seen_ids: set[int] = set()
        for er in matched.roles:
            rid = er.role_id
            if rid:
                seen_ids.add(rid)
            existing.append(
                RoleDraft(
                    role_id=rid,
                    role_name=er.role_name,
                    role_meta=er.role_meta,
                    sla_hours=er.sla_hours,
                    existing=True,
                    has_action_function=bool(er.has_action_function),
                    action_ui_schema=er.action_ui_schema,
                    action_function_schema=er.action_function_schema,
                    is_form_editable=er.is_form_editable or 0,
                )
            )
        incoming = [r for r in edge.roles if not r.existing]
        incoming_schema = edge.form_schema
        for r in incoming:
            if r.action_ui_schema:
                incoming_schema = r.action_ui_schema
                break
        if incoming_schema:
            edge.form_schema = incoming_schema
        if edge.update_form:
            edge.roles = existing
            if edge.target_role_id and edge.form_schema:
                for role in edge.roles:
                    if role.role_id == edge.target_role_id:
                        role.action_ui_schema = edge.form_schema
            continue
        if edge.delete_form:
            edge.roles = existing
            if edge.target_role_id:
                for role in edge.roles:
                    if role.role_id == edge.target_role_id:
                        role.action_ui_schema = None
            continue
        if edge.delete_role:
            edge.roles = [
                r
                for r in existing
                if not (edge.target_role_id and r.role_id == edge.target_role_id)
            ]
            continue
        if edge.delete_transition:
            edge.roles = existing
            continue
        if edge.update_roles:
            edge.roles = existing
            if edge.target_role_id and edge.update_sla_hours is not None:
                for role in edge.roles:
                    if role.role_id == edge.target_role_id:
                        role.sla_hours = edge.update_sla_hours
            continue
        edge.roles = existing + incoming
        if edge.append_roles and not incoming:
            edge.roles.append(RoleDraft())


def _hydrate_update_step(step, req: GraphAgentMessageRequest) -> None:
    node = None
    if step.existing_step_id:
        for n in req.graph.nodes:
            if n.step_id == step.existing_step_id:
                node = n
                break
    if node is None and step.name_ref:
        matched = match_node(step.name_ref, req.graph.nodes)
        if matched.unique and matched.item:
            step.existing_step_id = matched.item.id
            for n in req.graph.nodes:
                if n.step_id == matched.item.id:
                    node = n
                    break
    if node is None and len(req.graph.nodes) == 1:
        node = req.graph.nodes[0]
        step.existing_step_id = node.step_id
    if node is None:
        return
    if step.name_ref and (not step.name or step.name == step.name_ref or step.name == "Existing status"):
        step.name = node.name
    if step.sla_hours is None:
        step.sla_hours = node.sla_hours
    if not step.phase_name and step.phase_id is None:
        step.phase_id = node.phase_id
    if not step.officer_status_name and step.officer_status_id is None:
        step.officer_status_id = node.status_id
    if not step.citizen_status_name and step.citizen_status_id is None:
        step.citizen_status_id = node.citizen_status_id
    if not (step.description or "").strip():
        step.description = node.description or node.name
    step.is_initial = bool(node.is_initial)
    if step.step_type_id is None:
        step.step_type_id = node.step_type_id
    step.is_final = bool(node.is_final)


def resolve_spec(spec: WorkflowSpec, req: GraphAgentMessageRequest) -> list[Question]:
    questions: list[Question] = []
    catalogs = req.catalogs
    nodes = req.graph.nodes
    existing_initial = any(n.is_initial for n in nodes)
    _merge_existing_roles(spec, req)

    for i, step in enumerate(spec.steps):
        if step.delete:
            query = step.name_ref
            if query and query.strip().lower() in {
                "new status",
                "existing status",
                "status card",
            }:
                query = None
            if query:
                match = match_node(query, nodes)
                if match.unique and match.item:
                    step.existing_step_id = match.item.id
                    step.name = match.item.label
            if not step.existing_step_id:
                deletable = [n for n in nodes if not n.is_initial]
                if not deletable:
                    questions.append(
                        Question(
                            key=f"{step.key}:no_delete",
                            field="notice",
                            entity=step.key,
                            prompt="The initial status card cannot be deleted.",
                            options=[],
                            group_title="Remove status card",
                        )
                    )
                else:
                    options = [
                        QuestionOption(id=n.step_id, label=n.name)
                        for n in deletable
                        if n.step_id
                    ]
                    questions.append(
                        Question(
                            key=f"{step.key}:existing_step",
                            field="existing_step",
                            entity=step.key,
                            prompt="Which status card should be removed?",
                            options=options,
                            group_title="Remove status card",
                        )
                    )
                continue
            node = next((n for n in nodes if n.step_id == step.existing_step_id), None)
            if node:
                step.name = node.name
                if node.is_initial:
                    questions.append(
                        Question(
                            key=f"{step.key}:no_delete",
                            field="notice",
                            entity=step.key,
                            prompt="The initial status card cannot be deleted.",
                            options=[],
                            group_title="Remove status card",
                        )
                    )
            continue

        if step.update:
            _hydrate_update_step(step, req)
            if not step.existing_step_id:
                match = match_node(step.name_ref or step.name, nodes)
                questions.append(
                    question_from_match(
                        f"{step.key}:existing_step",
                        "existing_step",
                        step.key,
                        "Which existing status card should be updated?",
                        match,
                        group_title="Update status card",
                    )
                )
                continue
            if step.is_initial is None:
                step.is_initial = False
        else:
            if step.is_initial is None:
                step.is_initial = (not nodes and i == 0) and not existing_initial
            if existing_initial:
                step.is_initial = False

        group = f"Status card: {step.name}"
        is_new = not step.update
        placeholder_names = {"new status", "existing status", "status card"}

        if is_new and (
            not (step.name or "").strip()
            or step.name.strip().lower() in placeholder_names
        ):
            questions.append(
                Question(
                    key=f"{step.key}:name",
                    field="step_name",
                    entity=step.key,
                    prompt="Status name",
                    options=[],
                    group_title=group,
                )
            )

        phase = match_catalog(step.phase_name, catalogs.phases)
        if step.phase_id is None or (is_new and not step.phase_confirmed):
            default_pid = step.phase_id if step.phase_id else (phase.item.id if phase.item else None)
            questions.append(
                question_from_match(
                    f"{step.key}:phase",
                    "phase",
                    step.key,
                    "Phase",
                    phase,
                    group_title=group,
                    default_id=default_pid,
                )
            )

        officer = match_catalog(step.officer_status_name, catalogs.statuses)
        if step.officer_status_id is None or (is_new and not step.officer_status_confirmed):
            default_oid = step.officer_status_id if step.officer_status_id else (officer.item.id if officer.item else None)
            questions.append(
                question_from_match(
                    f"{step.key}:officer_status",
                    "officer_status",
                    step.key,
                    "Officer status",
                    officer,
                    group_title=group,
                    default_id=default_oid,
                )
            )

        citizen = match_catalog(step.citizen_status_name, catalogs.statuses)
        if step.citizen_status_id is None or (is_new and not step.citizen_status_confirmed):
            default_cid = step.citizen_status_id if step.citizen_status_id else (citizen.item.id if citizen.item else None)
            questions.append(
                question_from_match(
                    f"{step.key}:citizen_status",
                    "citizen_status",
                    step.key,
                    "Citizen status",
                    citizen,
                    group_title=group,
                    default_id=default_cid,
                )
            )

        if step.sla_hours is None or step.sla_hours <= 0 or (is_new and not step.sla_confirmed):
            suggested_sla = step.sla_hours if (step.sla_hours is not None and step.sla_hours > 0) else None
            questions.append(
                Question(
                    key=f"{step.key}:sla_hours",
                    field="sla_hours",
                    entity=step.key,
                    prompt="SLA hours",
                    options=[],
                    group_title=group,
                    default_value=suggested_sla,
                )
            )

        if not (step.description or "").strip() or (is_new and not step.description_confirmed):
            suggested_desc = (step.description or "").strip() or step.name
            questions.append(
                Question(
                    key=f"{step.key}:description",
                    field="description",
                    entity=step.key,
                    prompt="Status description",
                    options=[],
                    group_title=group,
                    default_value=suggested_desc,
                )
            )

        if is_new and not step.is_initial and (step.is_final is None or not step.is_final_confirmed):
            default_final = 1 if step.is_final else (0 if step.is_final is not None else None)
            questions.append(
                Question(
                    key=f"{step.key}:is_final",
                    field="is_final",
                    entity=step.key,
                    prompt="Is this a final status?",
                    options=[
                        QuestionOption(id=1, label="Yes"),
                        QuestionOption(id=0, label="No"),
                    ],
                    group_title=group,
                    default_id=default_final,
                )
            )

        if req.workflow_type_id == 19 and (step.step_type_id is None or (is_new and not step.step_type_confirmed)):
            st = match_catalog(step.step_type_name, catalogs.step_types)
            default_stid = step.step_type_id if step.step_type_id else (st.item.id if st.item else None)
            questions.append(
                question_from_match(
                    f"{step.key}:step_type",
                    "step_type",
                    step.key,
                    "Workflow step type",
                    st,
                    group_title=group,
                    default_id=default_stid,
                )
            )

    # If there are card questions, resolve status cards first before connecting
    if questions:
        return questions

    step_by_key = {s.key: s for s in spec.steps}
    step_by_name = {s.name.lower(): s for s in spec.steps}

    for index, edge in enumerate(spec.transitions, start=1):
        if not edge.roles:
            if not (
                edge.update_form
                or edge.update_roles
                or edge.delete_role
                or edge.delete_form
                or edge.delete_transition
            ):
                edge.roles = [RoleDraft()]

        title = f"Connection {index}: {_connection_title(edge, spec, nodes)}"
        needs_existing_link = bool(
            _is_edge_edit(edge) and not (edge.from_step_id and edge.to_step_id)
        )
        if edge.delete_form:
            group = "Remove custom action"
            action_prompt = "Which existing action has the custom action form to remove?"
        elif edge.delete_role:
            group = "Remove role"
            action_prompt = "Which existing action has the role to remove?"
        elif edge.delete_transition:
            group = "Remove action"
            action_prompt = "Which action should be removed?"
        elif edge.update_form:
            group = "Custom action form"
            action_prompt = "Which existing action should get the custom action form?"
        elif edge.update_roles:
            group = "Update role"
            action_prompt = "Which existing action has the role to update?"
        else:
            group = "Add role"
            action_prompt = "Which existing action should get the extra role?"
        if needs_existing_link:
            if not req.graph.edges:
                questions.append(
                    Question(
                        key="no_actions",
                        field="notice",
                        entity="session",
                        prompt=(
                            "There is no action on this workflow yet. Connect two status cards first."
                            if edge.update_form
                            else "There is no action on this workflow yet. Connect two status cards first, then add a role."
                        ),
                        options=[],
                        group_title=group,
                    )
                )
            else:
                options = []
                for i, ge in enumerate(req.graph.edges):
                    from_n = _node_name(ge.from_step_id, nodes) or str(ge.from_step_id)
                    to_n = _node_name(ge.to_step_id, nodes) or str(ge.to_step_id)
                    label = f"{ge.action_name or 'Action'}  ({from_n} → {to_n})"
                    options.append(QuestionOption(id=i, label=label))
                questions.append(
                    Question(
                        key=f"{edge.key}:existing_edge",
                        field="existing_edge",
                        entity=edge.key,
                        prompt=action_prompt,
                        options=options,
                        group_title=group,
                    )
                )
            skip_link_fields = True
            continue
        skip_link_fields = bool(
            _is_edge_edit(edge) and edge.from_step_id and edge.to_step_id
        )

        if not skip_link_fields and edge.from_step_id is None and edge.from_temp_key is None:
            src = (edge.from_ref or "").strip()
            if src in step_by_key:
                edge.from_temp_key = src
            elif src.lower() in step_by_name:
                edge.from_temp_key = step_by_name[src.lower()].key
            else:
                best_draft_step = None
                best_draft_score = 0.0
                for s in spec.steps:
                    score = _score(src, s.name)
                    if score > best_draft_score:
                        best_draft_score = score
                        best_draft_step = s
                if best_draft_step and best_draft_score >= 0.7:
                    edge.from_temp_key = best_draft_step.key
                else:
                    node = match_node(src, nodes)
                    if node.unique and node.item:
                        edge.from_step_id = node.item.id
                    else:
                        questions.append(
                            question_from_match(
                                f"{edge.key}:from",
                                "from_step",
                                edge.key,
                                "From status card",
                                node,
                                group_title=title,
                            )
                        )

        if not skip_link_fields and edge.to_step_id is None and edge.to_temp_key is None:
            dest = (edge.to_ref or "").strip()
            if dest in step_by_key:
                edge.to_temp_key = dest
            elif dest.lower() in step_by_name:
                edge.to_temp_key = step_by_name[dest.lower()].key
            else:
                best_draft_step = None
                best_draft_score = 0.0
                for s in spec.steps:
                    score = _score(dest, s.name)
                    if score > best_draft_score:
                        best_draft_score = score
                        best_draft_step = s
                if best_draft_step and best_draft_score >= 0.7:
                    edge.to_temp_key = best_draft_step.key
                else:
                    node = match_node(dest, nodes)
                    if node.unique and node.item:
                        edge.to_step_id = node.item.id
                    else:
                        questions.append(
                            question_from_match(
                                f"{edge.key}:to",
                                "to_step",
                                edge.key,
                                "To status card",
                                node,
                                group_title=title,
                            )
                        )

        # Refresh title after from/to may have resolved
        title = f"Connection {index}: {_connection_title(edge, spec, nodes)}"

        action = match_catalog(edge.action_name, catalogs.actions)
        if not skip_link_fields and (edge.action_id is None or not edge.action_confirmed):
            default_aid = edge.action_id if edge.action_id else (action.item.id if action.item else None)
            questions.append(
                question_from_match(
                    f"{edge.key}:action",
                    "action",
                    edge.key,
                    "Action",
                    action,
                    group_title=title,
                    default_id=default_aid,
                )
            )

        if req.workflow_type_id == 17:
            edge.department_id = req.department_id
        elif not skip_link_fields and (edge.department_id is None or not edge.department_confirmed):
            dept = match_catalog(edge.department_name, catalogs.departments)
            default_did = edge.department_id if edge.department_id else (dept.item.id if dept.item else None)
            questions.append(
                question_from_match(
                    f"{edge.key}:department",
                    "department",
                    edge.key,
                    "Department",
                    dept,
                    group_title=title,
                    default_id=default_did,
                )
            )

        for i, role in enumerate(edge.roles):
            if (
                edge.update_form
                or edge.update_roles
                or edge.delete_role
                or edge.delete_form
                or edge.delete_transition
            ):
                break
            if role.existing and role.role_id:
                continue
            role_label = role.role_name or "new role"
            role_group = group if edge.append_roles else title
            rm = match_catalog(role.role_name, catalogs.roles)
            if role.role_id is None or not role.role_confirmed:
                default_rid = role.role_id if role.role_id else (rm.item.id if rm.item else None)
                questions.append(
                    question_from_match(
                        f"{edge.key}:role:{i}",
                        "role",
                        edge.key,
                        "Which role should be added?",
                        rm,
                        group_title=role_group,
                        default_id=default_rid,
                    )
                )
            if not role.sla_confirmed:
                suggested_role_sla = role.sla_hours if (role.sla_hours is not None and role.sla_hours > 0) else None
                questions.append(
                    Question(
                        key=f"{edge.key}:role:{i}:sla",
                        field="role_sla",
                        entity=edge.key,
                        prompt=f"SLA hours for {role_label} (required)",
                        options=[],
                        group_title=role_group,
                        default_value=suggested_role_sla,
                    )
                )

        if (
            (edge.update_form or edge.update_roles or edge.delete_role or edge.delete_form)
            and edge.from_step_id
            and edge.to_step_id
        ):
            matched = None
            for ge in req.graph.edges:
                if ge.from_step_id == edge.from_step_id and ge.to_step_id == edge.to_step_id:
                    matched = ge
                    break
            graph_roles = list(matched.roles) if matched else []
            if edge.delete_form:
                graph_roles = [gr for gr in graph_roles if gr.action_ui_schema]
            if not edge.target_role_id:
                spoken = next(
                    (r.role_name for r in edge.roles if (not r.existing) and r.role_name),
                    None,
                )
                if spoken:
                    for gr in graph_roles:
                        label = (gr.role_name or "").lower()
                        if spoken.lower() in label or label in spoken.lower():
                            edge.target_role_id = gr.role_id
                            break
            if not edge.target_role_id and len(graph_roles) == 1:
                edge.target_role_id = graph_roles[0].role_id
            if not edge.target_role_id:
                role_opts = []
                for gr in graph_roles:
                    if not gr.role_id:
                        continue
                    role_opts.append(
                        QuestionOption(
                            id=gr.role_id,
                            label=gr.role_name or f"Role {gr.role_id}",
                        )
                    )
                if role_opts:
                    if edge.delete_role:
                        prompt = "Which role should be removed?"
                    elif edge.delete_form:
                        prompt = "Which role's custom action form should be removed?"
                    elif edge.update_roles:
                        prompt = "Which existing role should be updated?"
                    else:
                        prompt = "Which existing role should get the custom action form?"
                    questions.append(
                        Question(
                            key=f"{edge.key}:existing_role",
                            field="existing_role",
                            entity=edge.key,
                            prompt=prompt,
                            options=role_opts,
                            group_title=group,
                        )
                    )
                elif edge.delete_form:
                    questions.append(
                        Question(
                            key=f"{edge.key}:no_form",
                            field="notice",
                            entity=edge.key,
                            prompt="That action has no custom action form to remove.",
                            options=[],
                            group_title=group,
                        )
                    )
                elif not graph_roles:
                    questions.append(
                        Question(
                            key=f"{edge.key}:no_roles",
                            field="notice",
                            entity=edge.key,
                            prompt="That action has no roles yet.",
                            options=[],
                            group_title=group,
                        )
                    )
            if edge.delete_form and edge.target_role_id:
                for role in edge.roles:
                    if role.role_id == edge.target_role_id:
                        role.action_ui_schema = None
            if edge.delete_role and edge.target_role_id:
                edge.roles = [
                    r for r in edge.roles if r.role_id != edge.target_role_id
                ]
            if edge.update_form and edge.target_role_id and edge.form_schema:
                for role in edge.roles:
                    if role.role_id == edge.target_role_id:
                        role.action_ui_schema = edge.form_schema
            if edge.update_form and edge.target_role_id and not edge.form_schema:
                questions.append(
                    Question(
                        key=f"{edge.key}:form_fields",
                        field="form_fields_nl",
                        entity=edge.key,
                        prompt=(
                            "Describe the action form fields and any conditions, or paste Form Builder JSON. "
                            "Example: text field “Reason”, email field shown only when Reason is Other."
                        ),
                        options=[],
                        group_title=group,
                    )
                )
            if edge.update_roles and edge.target_role_id:
                if edge.update_sla_hours is not None:
                    for role in edge.roles:
                        if role.role_id == edge.target_role_id:
                            role.sla_hours = edge.update_sla_hours
                else:
                    target = next(
                        (r for r in edge.roles if r.role_id == edge.target_role_id),
                        None,
                    )
                    label = (target.role_name if target else None) or "this role"
                    questions.append(
                        Question(
                            key=f"{edge.key}:role:0:sla",
                            field="role_sla",
                            entity=edge.key,
                            prompt=f"New SLA hours for {label}",
                            options=[],
                            group_title=group,
                        )
                    )

    return questions
