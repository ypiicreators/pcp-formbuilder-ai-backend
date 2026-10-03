"""Tests for catalog matching and plan building."""

from app.graph_agent.models import (
    CatalogItem,
    CatalogsPayload,
    GraphAgentMessageRequest,
    GraphSnapshot,
)
from app.graph_agent.planner import build_plan
from app.graph_agent.resolve import match_catalog
from app.graph_agent.action_schema import fields_to_schema_json, normalize_form_fields
from app.graph_agent.parse import spec_from_llm_json
from app.graph_agent.spec import RoleDraft, StepDraft, TransitionDraft, WorkflowSpec


def test_unique_phase_match():
    items = [
        CatalogItem(id=1, label="Draft"),
        CatalogItem(id=2, label="Verification in Progress"),
    ]
    result = match_catalog("verification in progress", items)
    assert result.unique
    assert result.item and result.item.id == 2


def test_ambiguous_returns_options():
    items = [
        CatalogItem(id=1, label="Pending"),
        CatalogItem(id=2, label="Pending Verification"),
    ]
    result = match_catalog("Pending", items)
    assert result.options


def test_plan_create_step():
    spec = WorkflowSpec(
        steps=[
            StepDraft(
                key="step:clerk",
                name="Assigned to dealing clerk",
                phase_id=2,
                officer_status_id=3,
                citizen_status_id=4,
                sla_hours=100,
                description="verify",
                is_initial=True,
            )
        ]
    )
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="add card",
        workflowTypeId=17,
        graph=GraphSnapshot(),
    )
    ops = build_plan(spec, req)
    assert len(ops) == 1
    assert ops[0].op == "create_step"
    assert ops[0].payload["PhaseId"] == 2
    assert ops[0].payload["SlaHours"] == 100


def test_form_fields_keep_visible_when():
    fields = normalize_form_fields(
        [
            {
                "id": "reason",
                "type": "select",
                "label": {"en": "Reason", "pa": ""},
                "options": [{"value": "other", "label": {"en": "Other", "pa": ""}}],
            },
            {
                "id": "other_reason",
                "type": "text",
                "label": {"en": "Please specify", "pa": ""},
                "visibleWhen": {"reason": ["other"]},
                "clearOnHide": True,
            },
        ]
    )
    extra = next(f for f in fields if f["id"] == "other_reason")
    assert extra["visibleWhen"] == {"reason": ["other"]}
    assert extra["clearOnHide"] is True
    fields = normalize_form_fields(
        [
            {
                "id": "text_1790673446642",
                "type": "text",
                "label": {"en": "text Field", "pa": ""},
                "required": False,
                "placeholder": {"en": "Enter text", "pa": ""},
                "validators": [],
            },
            {
                "id": "email_1790673448750",
                "type": "email",
                "label": {"en": "email Field", "pa": ""},
                "required": False,
                "placeholder": {"en": "Enter email", "pa": ""},
                "validators": [{"type": "email", "message": "Invalid email format"}],
            },
        ]
    )
    assert [f["type"] for f in fields] == ["text", "email"]
    assert fields[0]["id"] == "text_1790673446642"
    assert fields[1]["validators"][0]["type"] == "email"


def test_plan_includes_action_ui_schema():
    schema = fields_to_schema_json([{"type": "text"}, {"type": "email"}])
    spec = WorkflowSpec(
        transitions=[
            TransitionDraft(
                key="edge:0",
                from_step_id=1,
                to_step_id=2,
                action_id=9,
                action_name="Mark Discrepancy",
                append_roles=True,
                roles=[
                    RoleDraft(role_id=3, role_name="Clerk", sla_hours=8, existing=True),
                    RoleDraft(
                        role_id=4,
                        role_name="Naib Tehsildar",
                        sla_hours=11,
                        action_ui_schema=schema,
                    ),
                ],
            )
        ]
    )
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="add role with text and email fields",
        graph=GraphSnapshot(),
    )
    ops = build_plan(spec, req)
    saved = ops[0].payload["roles"][1]["ActionUiSchema"]
    parsed = __import__("json").loads(saved)
    assert len(parsed) == 2
    assert parsed[1]["type"] == "email"


def test_llm_json_copies_form_fields():
    spec = spec_from_llm_json(
        {
            "create_transitions": [
                {
                    "from_ref": "A",
                    "to_ref": "B",
                    "action_name": "Mark Discrepancy",
                    "append_roles": True,
                    "roles": [
                        {
                            "role_name": "Naib Tehsildar",
                            "sla_hours": 11,
                            "form_fields": [{"type": "text"}, {"type": "email"}],
                        }
                    ],
                }
            ]
        },
        [],
    )
    assert spec.transitions[0].roles[0].action_ui_schema
    types = [
        f["type"]
        for f in __import__("json").loads(spec.transitions[0].roles[0].action_ui_schema)
    ]
    assert types == ["text", "email"]


def test_vague_add_role_parses_as_append():
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse(
        "i need to add one more role to the actions",
        [],
    )
    assert len(spec.transitions) == 1
    edge = spec.transitions[0]
    assert edge.append_roles is True
    assert edge.to_ref is None
    assert edge.from_ref is None
    assert edge.action_name is None


def test_vague_add_role_asks_which_existing_action():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("i need to add one more role to the actions", [])
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="i need to add one more role to the actions",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
                GraphNodeSnapshot(stepId=3, name="Approved"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                ),
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=3,
                    actionName="Recommended For Approval",
                    actionId=10,
                ),
            ],
        ),
        catalogs=CatalogsPayload(roles=[CatalogItem(id=4, label="Naib Tehsildar")]),
    )
    questions = resolve_spec(spec, req)
    assert any(q.field == "existing_edge" for q in questions)


def test_add_role_asks_catalog_role_after_picking_action():
    from app.graph_agent.binder import apply_answers, resolve_spec
    from app.graph_agent.models import GraphEdgeRole, GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("i need to add roles in the actions", [])
    assert spec.transitions
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="i need to add roles in the actions",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                    roles=[
                        GraphEdgeRole(roleId=7, roleName="Dealing Clerk", slaHours=8),
                    ],
                ),
            ],
        ),
        catalogs=CatalogsPayload(
            roles=[
                CatalogItem(
                    id=4,
                    label="Naib Tehsildar",
                    extra={"roleMeta": '{"Id":4,"Name":{"en":"Naib Tehsildar"}}'},
                ),
                CatalogItem(
                    id=7,
                    label="Dealing Clerk",
                    extra={"roleMeta": '{"Id":7,"Name":{"en":"Dealing Clerk"}}'},
                ),
            ]
        ),
    )
    questions = resolve_spec(spec, req)
    apply_answers(
        spec,
        {f"{spec.transitions[0].key}:existing_edge": 0},
        req.catalogs,
        req.graph,
    )
    questions = resolve_spec(spec, req)
    fields = [q.field for q in questions]
    assert "existing_edge" not in fields
    assert "role" in fields
    role_q = next(q for q in questions if q.field == "role")
    assert {o.label for o in role_q.options} == {"Naib Tehsildar", "Dealing Clerk"}
    assert "role_sla" in fields
    sla_q = next(q for q in questions if q.field == "role_sla")
    apply_answers(
        spec,
        {role_q.key: 4, sla_q.key: 12},
        req.catalogs,
        req.graph,
    )
    questions = resolve_spec(spec, req)
    assert questions == []
    new_roles = [r for r in spec.transitions[0].roles if not r.existing]
    assert len(new_roles) == 1
    assert new_roles[0].role_id == 4
    assert new_roles[0].sla_hours == 12


def test_custom_action_updates_existing_role_not_add_role():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphEdgeRole, GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("i need to add a custom actions in the role", [])
    assert spec.transitions
    edge = spec.transitions[0]
    assert edge.update_form is True
    assert edge.append_roles is False
    assert edge.roles == []

    req = GraphAgentMessageRequest(
        workflowId=55,
        text="i need to add a custom actions in the role",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned for application verification"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                    roles=[
                        GraphEdgeRole(roleId=4, roleName="Naib Tehsildar", slaHours=11),
                        GraphEdgeRole(roleId=7, roleName="Dealing Clerk", slaHours=8),
                    ],
                )
            ],
        ),
    )
    questions = resolve_spec(spec, req)
    fields = {q.field for q in questions}
    assert "existing_role" in fields
    assert "role" not in fields
    assert "role_sla" not in fields
    role_q = next(q for q in questions if q.field == "existing_role")
    assert {o.label for o in role_q.options} == {"Naib Tehsildar", "Dealing Clerk"}


def test_update_existing_card_plan_uses_step_id():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse(
        "update SLA of Assigned for application verification to 48 hours",
        [
            GraphNodeSnapshot(
                stepId=11,
                name="Assigned for application verification",
                slaHours=24,
                phaseId=2,
                statusId=3,
                citizenStatusId=4,
                isInitial=True,
            )
        ],
    )
    assert spec.steps
    assert spec.steps[0].update is True
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="update SLA of Assigned for application verification to 48 hours",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(
                    stepId=11,
                    name="Assigned for application verification",
                    slaHours=24,
                    phaseId=2,
                    statusId=3,
                    citizenStatusId=4,
                    isInitial=True,
                )
            ]
        ),
    )
    questions = resolve_spec(spec, req)
    assert questions == []
    ops = build_plan(spec, req)
    assert ops[0].payload["StepId"] == 11
    assert ops[0].payload["SlaHours"] == 48
    assert ops[0].payload["IsInitialStep"] == 1
    assert "Update status" in ops[0].summary


def test_new_status_card_asks_all_mandatory_fields():
    from app.graph_agent.binder import apply_answers, resolve_spec
    from app.graph_agent.models import GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("add a new status card named Review Pending", [])
    assert spec.steps
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="add a new status card named Review Pending",
        workflowTypeId=17,
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned", isInitial=True),
            ]
        ),
        catalogs=CatalogsPayload(
            phases=[CatalogItem(id=2, label="Verification in Progress")],
            statuses=[
                CatalogItem(id=3, label="Pending with Officer"),
                CatalogItem(id=4, label="Pending with Citizen"),
            ],
        ),
    )
    questions = resolve_spec(spec, req)
    fields = [q.field for q in questions]
    assert "phase" in fields
    assert "officer_status" in fields
    assert "citizen_status" in fields
    assert "sla_hours" in fields
    assert "description" in fields
    assert "is_final" in fields
    assert "step_type" not in fields
    apply_answers(
        spec,
        {
            next(q.key for q in questions if q.field == "phase"): 2,
            next(q.key for q in questions if q.field == "officer_status"): 3,
            next(q.key for q in questions if q.field == "citizen_status"): 4,
            next(q.key for q in questions if q.field == "sla_hours"): 24,
            next(q.key for q in questions if q.field == "is_final"): 0,
        },
        req.catalogs,
        req.graph,
        {next(q.key for q in questions if q.field == "description"): "field verification"},
    )
    assert resolve_spec(spec, req) == []


def test_new_status_card_asks_step_type_for_multi_department():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("add a new status card named Review Pending", [])
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="add a new status card named Review Pending",
        workflowTypeId=19,
        graph=GraphSnapshot(
            nodes=[GraphNodeSnapshot(stepId=1, name="Assigned", isInitial=True)]
        ),
        catalogs=CatalogsPayload(
            stepTypes=[CatalogItem(id=8, label="Department Step")],
        ),
    )
    fields = {q.field for q in resolve_spec(spec, req)}
    assert "step_type" in fields


def test_vague_add_status_card_asks_name_and_mandatory_fields():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("add new status card", [])
    assert spec.steps
    assert spec.steps[0].update is False
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="add new status card",
        workflowTypeId=17,
        graph=GraphSnapshot(
            nodes=[GraphNodeSnapshot(stepId=1, name="Assigned", isInitial=True)]
        ),
        catalogs=CatalogsPayload(
            phases=[CatalogItem(id=2, label="Verification in Progress")],
            statuses=[CatalogItem(id=3, label="Pending with Officer")],
        ),
    )
    fields = {q.field for q in resolve_spec(spec, req)}
    assert "step_name" in fields
    assert "phase" in fields
    assert "officer_status" in fields
    assert "citizen_status" in fields
    assert "sla_hours" in fields
    assert "description" in fields
    assert "is_final" in fields


def test_update_role_asks_existing_action():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphEdgeRole, GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("change role SLA to 20 hours", [])
    assert spec.transitions
    assert spec.transitions[0].update_roles is True
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="change role SLA to 20 hours",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                    roles=[
                        GraphEdgeRole(roleId=4, roleName="Naib Tehsildar", slaHours=11),
                        GraphEdgeRole(roleId=7, roleName="Dealing Clerk", slaHours=8),
                    ],
                )
            ],
        ),
    )
    questions = resolve_spec(spec, req)
    fields = {q.field for q in questions}
    assert "existing_role" in fields or "existing_edge" in fields
    assert "role" not in fields


def test_vague_remove_status_card_asks_which_card():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("remove the status card", [])
    assert spec.steps
    assert spec.steps[0].delete is True
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="remove the status card",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned", isInitial=True),
                GraphNodeSnapshot(stepId=2, name="Discrepancy", isInitial=False),
            ]
        ),
    )
    questions = resolve_spec(spec, req)
    assert any(q.field == "existing_step" for q in questions)
    assert {o.label for q in questions if q.field == "existing_step" for o in q.options} == {
        "Discrepancy"
    }


def test_cannot_delete_initial_status_card():
    from app.graph_agent.binder import resolve_spec
    from app.graph_agent.models import GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse

    spec = heuristic_parse("delete status card Assigned", [])
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="delete status card Assigned",
        graph=GraphSnapshot(
            nodes=[GraphNodeSnapshot(stepId=1, name="Assigned", isInitial=True)]
        ),
    )
    questions = resolve_spec(spec, req)
    assert any(q.field == "notice" for q in questions)


def test_vague_remove_role_asks_action_then_role():
    from app.graph_agent.binder import apply_answers, resolve_spec
    from app.graph_agent.models import GraphEdgeRole, GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse
    from app.graph_agent.planner import build_plan

    spec = heuristic_parse("remove the role", [])
    assert spec.transitions[0].delete_role is True
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="remove the role",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                    stepActionId=44,
                    roles=[
                        GraphEdgeRole(roleId=4, roleName="Naib Tehsildar", slaHours=11),
                        GraphEdgeRole(roleId=7, roleName="Dealing Clerk", slaHours=8),
                    ],
                )
            ],
        ),
        catalogs=CatalogsPayload(
            roles=[
                CatalogItem(id=4, label="Naib Tehsildar"),
                CatalogItem(id=7, label="Dealing Clerk"),
            ]
        ),
    )
    questions = resolve_spec(spec, req)
    assert any(q.field == "existing_edge" for q in questions) or spec.transitions[0].from_step_id
    if any(q.field == "existing_edge" for q in questions):
        apply_answers(spec, {f"{spec.transitions[0].key}:existing_edge": 0}, req.catalogs, req.graph)
        questions = resolve_spec(spec, req)
    role_q = next(q for q in questions if q.field == "existing_role")
    assert {o.label for o in role_q.options} == {"Naib Tehsildar", "Dealing Clerk"}
    apply_answers(spec, {role_q.key: 4}, req.catalogs, req.graph)
    assert resolve_spec(spec, req) == []
    ops = build_plan(spec, req)
    assert ops[0].op == "create_transition"
    ids = {r.get("RoleId") for r in ops[0].payload["roles"]}
    assert ids == {7}


def test_vague_remove_custom_action_asks_role_with_form():
    from app.graph_agent.binder import apply_answers, resolve_spec
    from app.graph_agent.models import GraphEdgeRole, GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse
    from app.graph_agent.planner import build_plan

    spec = heuristic_parse("remove the custom action", [])
    assert spec.transitions[0].delete_form is True
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="remove the custom action",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                    stepActionId=44,
                    roles=[
                        GraphEdgeRole(
                            roleId=4,
                            roleName="Naib Tehsildar",
                            slaHours=11,
                            actionUiSchema='[{"id":"text_1","type":"text"}]',
                        ),
                        GraphEdgeRole(roleId=7, roleName="Dealing Clerk", slaHours=8),
                    ],
                )
            ],
        ),
    )
    apply_answers(spec, {f"{spec.transitions[0].key}:existing_edge": 0}, req.catalogs, req.graph)
    questions = resolve_spec(spec, req)
    if questions:
        role_q = next(q for q in questions if q.field == "existing_role")
        apply_answers(spec, {role_q.key: 4}, req.catalogs, req.graph)
        questions = resolve_spec(spec, req)
    assert questions == []
    ops = build_plan(spec, req)
    schemas = {r.get("RoleId"): r.get("ActionUiSchema") for r in ops[0].payload["roles"]}
    assert schemas[4] is None
    assert 7 in schemas


def test_vague_remove_action_asks_which_edge():
    from app.graph_agent.binder import apply_answers, resolve_spec
    from app.graph_agent.models import GraphEdgeSnapshot, GraphNodeSnapshot
    from app.graph_agent.parse import heuristic_parse
    from app.graph_agent.planner import build_plan

    spec = heuristic_parse("delete this action", [])
    assert spec.transitions[0].delete_transition is True
    req = GraphAgentMessageRequest(
        workflowId=55,
        text="delete this action",
        graph=GraphSnapshot(
            nodes=[
                GraphNodeSnapshot(stepId=1, name="Assigned"),
                GraphNodeSnapshot(stepId=2, name="Discrepancy"),
            ],
            edges=[
                GraphEdgeSnapshot(
                    fromStepId=1,
                    toStepId=2,
                    actionName="Mark Discrepancy",
                    actionId=9,
                    stepActionId=44,
                )
            ],
        ),
    )
    questions = resolve_spec(spec, req)
    if any(q.field == "existing_edge" for q in questions):
        apply_answers(spec, {f"{spec.transitions[0].key}:existing_edge": 0}, req.catalogs, req.graph)
        questions = resolve_spec(spec, req)
    assert questions == []
    ops = build_plan(spec, req)
    assert ops[0].op == "delete_transition"
    assert ops[0].payload["stepActionId"] == 44


def test_prepare_document_upload_rejects_empty():
    from app.orchestration.document_flow import prepare_document_upload

    prep = prepare_document_upload(b"", "spec.pdf", "application/pdf")
    assert prep.error


def test_document_spec_from_llm_json():
    data = {
        "create_steps": [
            {"key": "step:submitted", "name": "Submitted", "sla_hours": 24},
            {"key": "step:approved", "name": "Approved", "is_final": True},
        ],
        "create_transitions": [
            {
                "from_ref": "Submitted",
                "to_ref": "Approved",
                "action_name": "Approve",
                "roles": [{"role_name": "Dealing Clerk", "sla_hours": 48}],
            }
        ],
    }
    spec = spec_from_llm_json(data, [])
    assert len(spec.steps) == 2
    assert len(spec.transitions) == 1
    assert spec.transitions[0].action_name == "Approve"


