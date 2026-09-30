"""Internal spec the agent fills before planning writes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StepDraft(BaseModel):
    key: str
    name: str
    name_ref: str | None = None
    update: bool = False
    existing_step_id: int | None = None
    phase_name: str | None = None
    officer_status_name: str | None = None
    citizen_status_name: str | None = None
    sla_hours: int | None = None
    description: str | None = None
    is_final: bool | None = None
    is_initial: bool | None = None
    step_type_name: str | None = None
    phase_id: int | None = None
    officer_status_id: int | None = None
    citizen_status_id: int | None = None
    step_type_id: int | None = None
    delete: bool = False


class RoleDraft(BaseModel):
    role_name: str | None = None
    sla_hours: int | None = None
    role_id: int | None = None
    role_meta: str | None = None
    existing: bool = False
    has_action_function: bool = False
    action_ui_schema: str | None = None
    action_function_schema: str | None = None
    is_form_editable: int = 0


class TransitionDraft(BaseModel):
    key: str
    from_ref: str | None = None
    to_ref: str | None = None
    action_name: str | None = None
    department_name: str | None = None
    action_id: int | None = None
    department_id: int | None = None
    from_step_id: int | None = None
    to_step_id: int | None = None
    to_temp_key: str | None = None
    action_type_id: int | None = None
    is_instant: bool = False
    append_roles: bool = False
    update_form: bool = False
    update_roles: bool = False
    update_sla_hours: int | None = None
    target_role_id: int | None = None
    form_schema: str | None = None
    form_fields_nl: str | None = None
    delete_role: bool = False
    delete_form: bool = False
    delete_transition: bool = False
    step_action_id: int | None = None
    roles: list[RoleDraft] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    steps: list[StepDraft] = Field(default_factory=list)
    transitions: list[TransitionDraft] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
