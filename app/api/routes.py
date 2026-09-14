"""
API routes for the PCP AI Form Builder.

EDIT mode (edit_json) is fully wired to the orchestration pipeline
(context selection -> LLM -> parse -> apply -> validate -> one repair). NL and
DOC modes still return a stub until they are wired.

NOTE (plan v3 §7.12): the Edit flow is a LINEAR pipeline, NOT a LangGraph graph.
LangGraph is reserved for the future Document mode only.
"""

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import get_settings
from app.models.schemas import (
    ClarificationResponse,
    EditRequest,
    ErrorResponse,
    GenerateNlRequest,
    GenerateOutcome,
    GenerateResponse,
    GenerationMode,
    HealthResponse,
)
from app.orchestration.edit_flow import EditStatus, run_edit_flow
from app.orchestration.generate_flow import GenerateStatus, run_generate_flow
from app.providers.base import LLMError
from app.providers.factory import get_provider

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    """Liveness check: confirms the server booted and reports the active provider."""
    settings = get_settings()
    return HealthResponse(
        app=settings.app_name,
        version=settings.app_version,
        provider=settings.llm_provider,
    )


def _stub_form(note: str) -> dict:
    """
    A minimal, CONTRACT-shaped placeholder form returned by the stub.

    Shape mirrors `jsonGenerator.ts::generateJSON()` exactly: starts at
    `sections`, carries no top-level metadata (serviceCode/title/category/
    version), and omits `order` on sections/fields.
    """
    return {
        "sections": [
            {
                "id": "stub_section",
                "title": {"en": "Stub Section", "pa": ""},
                "fields": [
                    {
                        "id": "stub_field",
                        "type": "text",
                        "label": {"en": note, "pa": ""},
                    }
                ],
            }
        ],
        "crossFieldValidators": [],
    }


@router.post(
    "/form-ai/generate",
    response_model=GenerateOutcome,
    responses={422: {"model": ErrorResponse}},
    tags=["form-ai"],
)
async def generate(
    mode: GenerationMode = Form(...),
    prompt: str | None = Form(default=None),
    base_json: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> GenerateOutcome:
    """
    Generate or edit a Form Builder configuration.

    Accepts multipart form-data so a single endpoint serves all three modes
    (document mode needs a file upload). STUB behaviour: validates the per-mode
    input rules from the API contract, then returns a canned proposal.
    """
    # --- Per-mode instruction rules (plan v3 §2.4 / API contract) ---------
    if mode == GenerationMode.GENERATE_NL:
        if not (prompt and prompt.strip()):
            raise HTTPException(status_code=422, detail="prompt is required for generate_nl")

    elif mode == GenerationMode.EDIT_JSON:
        if not (prompt and prompt.strip()):
            raise HTTPException(status_code=422, detail="prompt is required for edit_json")
        if not base_json:
            raise HTTPException(status_code=422, detail="base_json is required for edit_json")
        try:
            parsed_base = json.loads(base_json)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="base_json is not valid JSON")
        # Edit mode is fully wired: run the LLM orchestration pipeline.
        return await _run_edit(parsed_base, prompt)

    elif mode == GenerationMode.GENERATE_DOC:
        if file is None:
            raise HTTPException(status_code=422, detail="file is required for generate_doc")

    # --- STUB response for NL / DOC modes (not wired yet) -----------------
    warnings = ["STUB response: this mode is not wired yet."]
    return GenerateResponse(
        form=_stub_form(f"Stub for mode={mode.value}"),
        warnings=warnings,
        diff=None,
    )


@router.post(
    "/form-ai/generate-nl",
    response_model=GenerateResponse,
    responses={422: {"model": ErrorResponse}},
    tags=["form-ai"],
)
async def generate_nl(req: GenerateNlRequest) -> GenerateResponse:
    """
    Generate a complete form from a natural-language description (JSON body).

    Body: { "prompt": "describe the form and its fields/validations..." }
    Returns a proposal (a full sections-first form + warnings). No diff.
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt is required")

    try:
        provider = get_provider()
    except LLMError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    result = await run_generate_flow(req.prompt, provider)

    if result.status is GenerateStatus.FAILED:
        raise HTTPException(status_code=422, detail={"errors": result.errors})

    return GenerateResponse(
        form=result.form or {},
        warnings=result.warnings,
        diff=None,
    )


@router.post(
    "/form-ai/edit",
    response_model=GenerateOutcome,
    responses={422: {"model": ErrorResponse}},
    tags=["form-ai"],
)
async def edit(req: EditRequest) -> GenerateOutcome:
    """
    Edit an existing form via a clean JSON body (the frontend's primary entry).

    Body: { "prompt": "...", "base_json": { ...current form... } }
    Returns a proposal (form + diff + warnings) or a clarification request.
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt is required")
    if not isinstance(req.base_json, dict) or "sections" not in req.base_json:
        raise HTTPException(
            status_code=422,
            detail="base_json must be a form object with a 'sections' array",
        )
    return await _run_edit(req.base_json, req.prompt)


async def _run_edit(base_json: dict, instruction: str) -> GenerateOutcome:
    """Drive the edit-mode pipeline and map its result to an HTTP response."""
    try:
        provider = get_provider()
    except LLMError as exc:
        # Misconfigured provider (e.g. missing credentials) -> 422 with a clear
        # message rather than a 500.
        raise HTTPException(status_code=422, detail=str(exc))

    result = await run_edit_flow(base_json, instruction, provider)

    if result.status is EditStatus.NEEDS_CLARIFICATION:
        return ClarificationResponse(
            question="Which field did you mean?",
            options=result.clarification,
            allow_all=False,
        )

    if result.status is EditStatus.FAILED:
        raise HTTPException(status_code=422, detail={"errors": result.errors})

    return GenerateResponse(
        form=result.form or {},
        warnings=result.warnings,
        diff=result.diff,
    )
