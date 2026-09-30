"""Turn a fully resolved spec into ordered frontend apply ops."""

from __future__ import annotations

import json

from app.graph_agent.action_schema import fields_to_schema_json
from app.graph_agent.models import GraphAgentMessageRequest, PlanOp
from app.graph_agent.spec import WorkflowSpec


def _as_json_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    for _ in range(4):
        if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                break
            if isinstance(parsed, str):
                s = parsed.strip()
                continue
            if isinstance(parsed, (dict, list)):
                return json.dumps(parsed, ensure_ascii=False)
            break
        break
    if s[0] in "{[":
        try:
            parsed = json.loads(s)
            if isinstance(parsed, (dict, list)):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            return s
        return s
    return None


def build_plan(spec: WorkflowSpec, req: GraphAgentMessageRequest) -> list[PlanOp]:
    ops: list[PlanOp] = []
    for step in spec.steps:
        if step.delete:
            if not step.existing_step_id:
                continue
            ops.append(
                PlanOp(
                    op="delete_step",
                    summary=f"Remove status “{step.name}”",
                    temp_key=step.key,
                    payload={"StepId": step.existing_step_id},
                )
            )
            continue
        is_update = bool(step.update and step.existing_step_id)
        ops.append(
            PlanOp(
                op="create_step",
                summary=(
                    f"Update status “{step.name}”"
                    if is_update
                    else f"Create status “{step.name}”"
                ),
                temp_key=step.key,
                payload={
                    "StepId": step.existing_step_id if is_update else 0,
                    "WorkflowId": req.workflow_id,
                    "StepName": step.name,
                    "PhaseId": step.phase_id,
                    "StatusId": step.officer_status_id,
                    "CitizenStatusId": step.citizen_status_id,
                    "IsInitialStep": 1 if step.is_initial else 0,
                    "IsFinalStep": 1 if step.is_final else 0,
                    "SlaHours": step.sla_hours or 0,
                    "StepDescription": (step.description or step.name).strip(),
                    "StepTypeId": step.step_type_id
                    if req.workflow_type_id == 19
                    else 0,
                    "assignmentRule": [],
                },
            )
        )

    for edge in spec.transitions:
        if edge.delete_transition or (edge.delete_role and not edge.roles):
            ops.append(
                PlanOp(
                    op="delete_transition",
                    summary=(
                        f"Remove action “{edge.action_name or 'action'}” "
                        f"({edge.from_ref or edge.from_step_id} → "
                        f"{edge.to_ref or edge.to_step_id})"
                        + (" — last role" if edge.delete_role else "")
                    ),
                    from_ref=str(edge.from_step_id) if edge.from_step_id else edge.from_ref,
                    to_ref=str(edge.to_step_id) if edge.to_step_id else edge.to_ref,
                    payload={
                        "workflowId": req.workflow_id,
                        "stepActionId": edge.step_action_id,
                    },
                )
            )
            continue
        if not edge.action_type_id and edge.action_id:
            for item in req.catalogs.actions:
                if item.id == edge.action_id:
                    extra = item.extra or {}
                    edge.action_type_id = extra.get("actionTypeId")
                    if extra.get("isInstantAction") is not None:
                        edge.is_instant = bool(extra.get("isInstantAction"))
                    break
        if req.workflow_type_id == 17 and not edge.department_id:
            edge.department_id = req.department_id

        roles = []
        for role in edge.roles:
            meta = None
            if role.role_id:
                for item in req.catalogs.roles:
                    if item.id == role.role_id:
                        extra = item.extra or {}
                        meta = extra.get("roleMeta")
                        break
            if not meta:
                meta = role.role_meta
            meta = _as_json_text(meta)
            if not meta and role.role_id:
                meta = json.dumps(
                    {
                        "Id": role.role_id,
                        "Name": {"en": role.role_name or "", "pa": "", "hi": ""},
                    },
                    ensure_ascii=False,
                )
            if not meta:
                continue
            schema = role.action_ui_schema
            if schema and not isinstance(schema, str):
                schema = fields_to_schema_json(schema)
            elif schema:
                unwrapped = _as_json_text(schema)
                if unwrapped:
                    schema = unwrapped
                elif schema.strip() and schema.strip()[0] not in "[{":
                    schema = fields_to_schema_json(schema)
            roles.append(
                {
                    "ActionRoleSLAHours": role.sla_hours or 0,
                    "RoleMeta": meta,
                    "HasActionFunction": role.has_action_function,
                    "IsInstantAction": 1 if edge.is_instant else 0,
                    "IsFormEditable": role.is_form_editable or 0,
                    "ActionUiSchema": schema or None,
                    "ActionFunctionSchema": role.action_function_schema,
                    "RoleId": role.role_id,
                }
            )
        role_bits = []
        for r in edge.roles:
            if not (r.role_id or r.role_name or r.role_meta):
                continue
            bit = r.role_name or "?"
            if r.action_ui_schema:
                bit += " + action form"
            role_bits.append(bit)
        ops.append(
            PlanOp(
                op="create_transition",
                summary=(
                    f"{'Remove action form on' if edge.delete_form else ('Remove role on' if edge.delete_role else ('Add action form on' if edge.update_form else ('Update role on' if edge.update_roles else ('Update roles on' if edge.append_roles else 'Connect'))))} "
                    f"{(edge.from_ref or edge.from_step_id)} → "
                    f"{(edge.to_ref or edge.to_temp_key or edge.to_step_id)} "
                    f"({edge.action_name or 'action'}; "
                    f"roles: {', '.join(role_bits)})"
                ),
                from_ref=str(edge.from_step_id) if edge.from_step_id else edge.from_ref,
                to_ref=edge.to_temp_key or (
                    str(edge.to_step_id) if edge.to_step_id else edge.to_ref
                ),
                payload={
                    "workflow": {"workflowId": req.workflow_id},
                    "currentStep": {"stepId": edge.from_step_id},
                    "nextStep": {
                        "stepId": edge.to_step_id,
                        "tempKey": edge.to_temp_key,
                    },
                    "stepAction": {
                        "actionId": edge.action_id,
                        "IsInstantAction": 1 if edge.is_instant else 0,
                        "ActionTypeId": edge.action_type_id,
                        "DepartmentId": edge.department_id,
                        "WorkflowTypeId": req.workflow_type_id,
                        "MailEventId": 0,
                        "SMSEventId": 0,
                    },
                    "roles": roles,
                    "transition": {
                        "isRollback": 0,
                        "isParallel": 0,
                        "isCrossDept": 0,
                    },
                },
            )
        )
    return ops
