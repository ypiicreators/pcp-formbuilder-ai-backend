"""Request/response contract for the workflow graph agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("en", "En", "Name", "name"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner
            if isinstance(inner, dict):
                nested = _as_text(inner)
                if nested:
                    return nested
        for inner in value.values():
            nested = _as_text(inner)
            if nested:
                return nested
        return None
    return str(value)


class GraphNodeSnapshot(BaseModel):
    step_id: int = Field(alias="stepId")
    name: str
    is_initial: bool | None = Field(default=None, alias="isInitial")
    is_final: bool | None = Field(default=None, alias="isFinal")
    sla_hours: int | None = Field(default=None, alias="slaHours")
    phase_id: int | None = Field(default=None, alias="phaseId")
    status_id: int | None = Field(default=None, alias="statusId")
    citizen_status_id: int | None = Field(default=None, alias="citizenStatusId")
    description: str | None = None
    step_type_id: int | None = Field(default=None, alias="stepTypeId")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    @field_validator("name", mode="before")
    @classmethod
    def _name_text(cls, v: Any) -> Any:
        return _as_text(v) or ""


class GraphEdgeRole(BaseModel):
    role_id: int | None = Field(default=None, alias="roleId")
    role_name: str | None = Field(default=None, alias="roleName")
    role_meta: str | None = Field(default=None, alias="roleMeta")
    sla_hours: int | None = Field(default=None, alias="slaHours")
    action_ui_schema: str | None = Field(default=None, alias="actionUiSchema")
    action_function_schema: str | None = Field(
        default=None, alias="actionFunctionSchema"
    )
    has_action_function: bool | None = Field(
        default=None, alias="hasActionFunction"
    )
    is_form_editable: int | None = Field(default=None, alias="isFormEditable")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    @field_validator("role_name", mode="before")
    @classmethod
    def _role_name_text(cls, v: Any) -> Any:
        return _as_text(v)

    @field_validator("role_meta", mode="before")
    @classmethod
    def _role_meta_text(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            return v
        try:
            import json

            return json.dumps(v)
        except Exception:
            return str(v)

    @field_validator("action_ui_schema", mode="before")
    @classmethod
    def _schema_text(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return v
        try:
            import json

            return json.dumps(v)
        except Exception:
            return str(v)


class GraphEdgeSnapshot(BaseModel):
    from_step_id: int = Field(alias="fromStepId")
    to_step_id: int = Field(alias="toStepId")
    action_name: str | None = Field(default=None, alias="actionName")
    action_id: int | None = Field(default=None, alias="actionId")
    action_type_id: int | None = Field(default=None, alias="actionTypeId")
    is_instant: bool | None = Field(default=None, alias="isInstant")
    department_id: int | None = Field(default=None, alias="departmentId")
    sms_event_id: int | None = Field(default=None, alias="smsEventId")
    mail_event_id: int | None = Field(default=None, alias="mailEventId")
    step_action_id: int | None = Field(default=None, alias="stepActionId")
    roles: list[GraphEdgeRole] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    @field_validator("action_name", mode="before")
    @classmethod
    def _action_name_text(cls, v: Any) -> Any:
        return _as_text(v)


class CatalogItem(BaseModel):
    id: int
    label: str
    extra: dict[str, Any] | None = None

    @field_validator("label", mode="before")
    @classmethod
    def _label_text(cls, v: Any) -> Any:
        return _as_text(v) or ""


class CatalogsPayload(BaseModel):
    phases: list[CatalogItem] = Field(default_factory=list)
    statuses: list[CatalogItem] = Field(default_factory=list)
    actions: list[CatalogItem] = Field(default_factory=list)
    roles: list[CatalogItem] = Field(default_factory=list)
    departments: list[CatalogItem] = Field(default_factory=list)
    step_types: list[CatalogItem] = Field(default_factory=list, alias="stepTypes")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class AnswerItem(BaseModel):
    key: str
    id: int | None = None
    text: str | None = None


class GraphSnapshot(BaseModel):
    nodes: list[GraphNodeSnapshot] = Field(default_factory=list)
    edges: list[GraphEdgeSnapshot] = Field(default_factory=list)


class GraphAgentMessageRequest(BaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")
    workflow_id: int = Field(alias="workflowId")
    text: str = Field(default="")
    workflow_type_id: int | None = Field(default=None, alias="workflowTypeId")
    department_id: int | None = Field(default=None, alias="departmentId")
    graph: GraphSnapshot = Field(default_factory=GraphSnapshot)
    catalogs: CatalogsPayload = Field(default_factory=CatalogsPayload)
    answers: list[AnswerItem] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class QuestionOption(BaseModel):
    id: int
    label: str


class Question(BaseModel):
    key: str
    field: str
    entity: str
    prompt: str
    options: list[QuestionOption] = Field(default_factory=list)
    group_title: str | None = Field(default=None, alias="groupTitle")
    default_id: int | None = Field(default=None, alias="defaultId")
    default_value: Any | None = Field(default=None, alias="defaultValue")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class PlanOp(BaseModel):
    op: Literal["create_step", "create_transition", "delete_step", "delete_transition"]
    summary: str
    temp_key: str | None = Field(default=None, alias="tempKey")
    from_ref: str | None = Field(default=None, alias="fromRef")
    to_ref: str | None = Field(default=None, alias="toRef")
    payload: dict[str, Any]

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class GraphAgentMessageResponse(BaseModel):
    status: Literal["needs_input", "needs_confirm", "error"]
    session_id: str = Field(alias="sessionId")
    message: str = ""
    questions: list[Question] = Field(default_factory=list)
    plan: list[PlanOp] = Field(default_factory=list)
    plan_id: str | None = Field(default=None, alias="planId")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
