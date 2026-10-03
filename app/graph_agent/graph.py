"""LangGraph pipeline: parse → bind answers → resolve → plan."""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.graph_agent.binder import apply_answers, resolve_spec
from app.graph_agent.models import (
    GraphAgentMessageRequest,
    GraphAgentMessageResponse,
)
from app.graph_agent.parse import parse_message
from app.graph_agent.planner import build_plan
from app.graph_agent.spec import WorkflowSpec

_SESSIONS: dict[str, dict[str, Any]] = {}


class AgentState(TypedDict, total=False):
    req: GraphAgentMessageRequest
    session_id: str
    initial_spec: WorkflowSpec
    spec: WorkflowSpec
    questions: list
    plan: list
    status: str
    message: str


def _session(session_id: str | None) -> str:
    sid = session_id or str(uuid.uuid4())
    _SESSIONS.setdefault(sid, {})
    return sid


async def node_parse(state: AgentState) -> dict:
    from app.graph_agent.action_schema import interpret_form_fields

    req = state["req"]
    sid = state["session_id"]
    stored = _SESSIONS.get(sid, {})
    stored_raw = stored.get("spec")
    stored_spec = (
        WorkflowSpec.model_validate(stored_raw) if stored_raw else None
    )
    waiting_for_form = bool(
        stored_spec
        and any(
            e.update_form and e.target_role_id and not e.form_schema
            for e in stored_spec.transitions
        )
    )
    initial = state.get("initial_spec")
    if initial is not None:
        spec = initial
    elif (req.answers or waiting_for_form) and stored_spec:
        spec = stored_spec
    else:
        spec = await parse_message(req)

    id_answers = {a.key: a.id for a in req.answers if a.id is not None}
    text_answers = {
        a.key: a.text for a in req.answers if a.text and str(a.text).strip()
    }
    if waiting_for_form and req.text.strip() and not text_answers:
        for edge in spec.transitions:
            if edge.update_form and edge.target_role_id and not edge.form_schema:
                text_answers[f"{edge.key}:form_fields"] = req.text.strip()
                break
    if id_answers or text_answers:
        apply_answers(spec, id_answers, req.catalogs, req.graph, text_answers)

    for edge in spec.transitions:
        blob = edge.form_fields_nl
        if edge.update_form and not edge.form_schema and blob:
            schema = await interpret_form_fields(blob)
            if schema:
                edge.form_schema = schema
                edge.form_fields_nl = None
    return {"spec": spec}


def node_resolve(state: AgentState) -> dict:
    questions = resolve_spec(state["spec"], state["req"])
    if questions:
        _SESSIONS[state["session_id"]] = {
            "spec": state["spec"].model_dump(),
            "workflow_id": state["req"].workflow_id,
        }
        return {
            "questions": questions,
            "status": "needs_input",
            "message": (
                "Describe the action form fields and any show/hide conditions, or paste Form Builder JSON. A dropdown cannot capture those conditions."
                if any(getattr(q, "field", None) == "form_fields_nl" for q in questions)
                else "Select the highlighted values to continue."
            ),
        }
    return {"questions": [], "status": "needs_confirm"}


def node_plan(state: AgentState) -> dict:
    if state.get("status") == "needs_input":
        return {}
    plan = build_plan(state["spec"], state["req"])
    plan_id = str(uuid.uuid4())
    _SESSIONS[state["session_id"]] = {
        "spec": state["spec"].model_dump(),
        "plan_id": plan_id,
        "workflow_id": state["req"].workflow_id,
    }
    if not plan:
        return {
            "status": "error",
            "message": "Could not understand that request. Name the status card to add, or say which action should get another role.",
            "plan": [],
        }
    return {
        "plan": plan,
        "status": "needs_confirm",
        "message": "Review the plan, then confirm to save using the workflow APIs.",
        "plan_id": plan_id,
    }


def _after_resolve(state: AgentState) -> str:
    if state.get("status") == "needs_input":
        return "stop"
    return "plan"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("parse", node_parse)
    g.add_node("resolve", node_resolve)
    g.add_node("plan", node_plan)
    g.set_entry_point("parse")
    g.add_edge("parse", "resolve")
    g.add_conditional_edges("resolve", _after_resolve, {"stop": END, "plan": "plan"})
    g.add_edge("plan", END)
    return g.compile()


_GRAPH = build_graph()


async def run_graph_agent(
    req: GraphAgentMessageRequest,
    *,
    initial_spec: WorkflowSpec | None = None,
) -> GraphAgentMessageResponse:
    sid = _session(req.session_id)
    if req.workflow_id <= 0:
        return GraphAgentMessageResponse(
            status="error",
            session_id=sid,
            message="Save the workflow definition first, then use the AI agent to add cards.",
        )
    invoke: AgentState = {"req": req, "session_id": sid}
    if initial_spec is not None:
        invoke["initial_spec"] = initial_spec
    result = await _GRAPH.ainvoke(invoke)
    return GraphAgentMessageResponse(
        status=result.get("status") or "error",
        session_id=sid,
        message=result.get("message") or "",
        questions=result.get("questions") or [],
        plan=result.get("plan") or [],
        plan_id=result.get("plan_id"),
    )
