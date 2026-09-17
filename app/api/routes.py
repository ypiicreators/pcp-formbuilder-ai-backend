"""
API routes for the PCP AI Form Builder.

UNIFIED CHAT ENTRY (current): POST /api/form-ai/assist is the single endpoint the
frontend chat uses. It infers the mode from the request:
  - base_json ABSENT  -> GENERATE a new form from the prompt (first message)
  - base_json PRESENT -> EDIT that form with the prompt (every later message)
Document mode is deferred (not wired yet).

The older per-mode endpoints (/generate multipart, /generate-nl, /edit) are
COMMENTED OUT below — kept for reference, superseded by /assist. The underlying
flows (run_edit_flow, run_generate_flow) are unchanged and reused here.

NOTE (plan v3 §7.12): the flows are LINEAR pipelines, NOT a LangGraph graph.
"""

# import json  # (was used by the commented-out multipart /generate endpoint)

import json as _json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.models.schemas import (
    AssistRequest,
    ClarificationResponse,
    # EditRequest,        # (commented-out /edit endpoint)
    ErrorResponse,
    # GenerateNlRequest,  # (commented-out /generate-nl endpoint)
    GenerateOutcome,
    GenerateResponse,
    # GenerationMode,     # (commented-out multipart /generate endpoint)
    HealthResponse,
)
from app.orchestration.document_flow import (
    run_document_flow,
    run_document_flow_stream,
)
from app.orchestration.edit_flow import EditStatus, run_edit_flow
from app.orchestration.generate_flow import GenerateStatus, run_generate_flow
from app.providers.base import LLMError
from app.providers.factory import get_provider

#: Max upload accepted by the document endpoint (pre-conversion). A generous cap
#: that still guards against absurd uploads; the flow enforces a tighter
#: post-conversion limit before sending to the model.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

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


# --- Unified chat endpoint --------------------------------------------------


@router.post(
    "/form-ai/assist",
    response_model=GenerateOutcome,
    responses={422: {"model": ErrorResponse}},
    tags=["form-ai"],
)
async def assist(req: AssistRequest) -> GenerateOutcome:
    """
    Single entry for the AI Assist chat.

    Body: { "prompt": "...", "base_json"?: { ...current form... } }
      - no base_json  -> GENERATE a new form from the prompt
      - with base_json -> EDIT that form with the prompt
    Returns a proposal (form + diff? + warnings) or a clarification request.
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt is required")

    # EDIT: a form was provided -> change it.
    if req.base_json is not None:
        if not isinstance(req.base_json, dict) or "sections" not in req.base_json:
            raise HTTPException(
                status_code=422,
                detail="base_json must be a form object with a 'sections' array",
            )
        return await _run_edit(req.base_json, req.prompt, req.target_field_ids)

    # GENERATE: no form yet -> create one from the description.
    return await _run_generate(req.prompt)


# --- Document upload endpoint (multipart) -----------------------------------


@router.post(
    "/form-ai/assist-document",
    response_model=GenerateOutcome,
    responses={422: {"model": ErrorResponse}},
    tags=["form-ai"],
)
async def assist_document(
    file: UploadFile = File(..., description="The source document (PDF, DOCX, image, or text)."),
    prompt: str | None = Form(default=None, description="Optional instruction; a server default is used if blank."),
) -> GenerateOutcome:
    """
    Document-mode entry (multipart/form-data).

    The admin uploads a source document (certificate, existing form, output
    template) and the AI infers the application form a citizen would fill in to
    produce/obtain it. PDFs/images/text go straight to the model; DOCX is
    converted to PDF server-side first.

    Kept separate from /form-ai/assist because that endpoint carries a JSON body
    (a form object), while this one carries a binary upload — two different
    transports on one handler is not expressible cleanly. The frontend picks the
    endpoint by whether a file is attached.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    if len(data) > _MAX_UPLOAD_BYTES:
        mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=422, detail=f"File too large (limit {mb} MB).")

    try:
        provider = get_provider()
    except LLMError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    result = await run_document_flow(
        file_bytes=data,
        filename=file.filename or "document",
        content_type=file.content_type,
        instruction=prompt,
        provider=provider,
    )
    if result.status is GenerateStatus.FAILED:
        raise HTTPException(status_code=422, detail={"errors": result.errors})

    # Document mode creates a whole form (like generate): no "before" diff.
    return GenerateResponse(form=result.form or {}, warnings=result.warnings, diff=None)


@router.post("/form-ai/assist-document-stream", tags=["form-ai"])
async def assist_document_stream(
    file: UploadFile = File(..., description="The source document (PDF, DOCX, image, or text)."),
    prompt: str | None = Form(default=None, description="Optional instruction; a server default is used if blank."),
) -> StreamingResponse:
    """
    Streaming document-mode entry (Server-Sent Events).

    Same input as /form-ai/assist-document, but the response is an SSE stream so
    the UI can show the form building live instead of waiting ~minutes on a
    spinner. Events (one JSON object per `data:` line):

        {"type": "delta", "text": "..."}   # a text fragment as the model writes
        {"type": "done",  "form": {...}}   # the validated, apply-ready form
        {"type": "error", "errors": [...]}  # unrecoverable failure

    The `delta` events are for DISPLAY ONLY; the client applies the form from the
    `done` event (validated server-side on the complete output). Errors are sent
    as an `error` event, NOT an HTTP error, because the 200 response has already
    started streaming by the time a failure can be known.
    """
    data = await file.read()

    async def event_source():
        # Guard rails first — emit as an error event (stream has 200 status).
        if not data:
            yield _sse({"type": "error", "errors": ["The uploaded file is empty."]})
            return
        if len(data) > _MAX_UPLOAD_BYTES:
            mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
            yield _sse({"type": "error", "errors": [f"File too large (limit {mb} MB)."]})
            return
        try:
            provider = get_provider()
        except LLMError as exc:
            yield _sse({"type": "error", "errors": [str(exc)]})
            return

        try:
            async for event, payload in run_document_flow_stream(
                file_bytes=data,
                filename=file.filename or "document",
                content_type=file.content_type,
                instruction=prompt,
                provider=provider,
            ):
                if event == "delta":
                    yield _sse({"type": "delta", "text": payload})
                elif event == "done":
                    yield _sse({"type": "done", "form": payload})
                elif event == "error":
                    yield _sse({"type": "error", "errors": payload})
        except Exception as exc:  # never leak a raw traceback into the stream
            yield _sse({"type": "error", "errors": [f"Streaming failed: {exc}"]})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx) for live flush
        },
    )


def _sse(obj: dict) -> str:
    """Serialise one object as a single SSE `data:` event."""
    return f"data: {_json.dumps(obj, ensure_ascii=False)}\n\n"


async def _run_generate(description: str) -> GenerateOutcome:
    """Drive the generate-mode pipeline and map its result to an HTTP response."""
    try:
        provider = get_provider()
    except LLMError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    result = await run_generate_flow(description, provider)
    if result.status is GenerateStatus.FAILED:
        raise HTTPException(status_code=422, detail={"errors": result.errors})

    return GenerateResponse(form=result.form or {}, warnings=result.warnings, diff=None)


async def _run_edit(
    base_json: dict, instruction: str, target_field_ids: list[str] | None = None
) -> GenerateOutcome:
    """Drive the edit-mode pipeline and map its result to an HTTP response."""
    try:
        provider = get_provider()
    except LLMError as exc:
        # Misconfigured provider (e.g. missing credentials) -> 422 with a clear
        # message rather than a 500.
        raise HTTPException(status_code=422, detail=str(exc))

    result = await run_edit_flow(
        base_json, instruction, provider, target_field_ids=target_field_ids
    )

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


# ============================================================================
# LEGACY / SUPERSEDED ENDPOINTS — commented out, kept for reference.
# All of these are now handled by POST /form-ai/assist above.
# ============================================================================
#
# def _stub_form(note: str) -> dict:
#     """A minimal, CONTRACT-shaped placeholder form (sections-first, no metadata)."""
#     return {
#         "sections": [
#             {
#                 "id": "stub_section",
#                 "title": {"en": "Stub Section", "pa": ""},
#                 "fields": [
#                     {"id": "stub_field", "type": "text", "label": {"en": note, "pa": ""}}
#                 ],
#             }
#         ],
#         "crossFieldValidators": [],
#     }
#
#
# @router.post(
#     "/form-ai/generate",
#     response_model=GenerateOutcome,
#     responses={422: {"model": ErrorResponse}},
#     tags=["form-ai"],
# )
# async def generate(
#     mode: GenerationMode = Form(...),
#     prompt: str | None = Form(default=None),
#     base_json: str | None = Form(default=None),
#     file: UploadFile | None = File(default=None),
# ) -> GenerateOutcome:
#     """Multipart entry serving all three modes. Superseded by /form-ai/assist."""
#     if mode == GenerationMode.GENERATE_NL:
#         if not (prompt and prompt.strip()):
#             raise HTTPException(status_code=422, detail="prompt is required for generate_nl")
#     elif mode == GenerationMode.EDIT_JSON:
#         if not (prompt and prompt.strip()):
#             raise HTTPException(status_code=422, detail="prompt is required for edit_json")
#         if not base_json:
#             raise HTTPException(status_code=422, detail="base_json is required for edit_json")
#         try:
#             parsed_base = json.loads(base_json)
#         except (TypeError, ValueError):
#             raise HTTPException(status_code=422, detail="base_json is not valid JSON")
#         return await _run_edit(parsed_base, prompt)
#     elif mode == GenerationMode.GENERATE_DOC:
#         if file is None:
#             raise HTTPException(status_code=422, detail="file is required for generate_doc")
#     warnings = ["STUB response: this mode is not wired yet."]
#     return GenerateResponse(form=_stub_form(f"Stub for mode={mode.value}"), warnings=warnings, diff=None)
#
#
# @router.post(
#     "/form-ai/generate-nl",
#     response_model=GenerateResponse,
#     responses={422: {"model": ErrorResponse}},
#     tags=["form-ai"],
# )
# async def generate_nl(req: GenerateNlRequest) -> GenerateResponse:
#     """Generate from a description (JSON body). Superseded by /form-ai/assist (no base_json)."""
#     if not req.prompt.strip():
#         raise HTTPException(status_code=422, detail="prompt is required")
#     try:
#         provider = get_provider()
#     except LLMError as exc:
#         raise HTTPException(status_code=422, detail=str(exc))
#     result = await run_generate_flow(req.prompt, provider)
#     if result.status is GenerateStatus.FAILED:
#         raise HTTPException(status_code=422, detail={"errors": result.errors})
#     return GenerateResponse(form=result.form or {}, warnings=result.warnings, diff=None)
#
#
# @router.post(
#     "/form-ai/edit",
#     response_model=GenerateOutcome,
#     responses={422: {"model": ErrorResponse}},
#     tags=["form-ai"],
# )
# async def edit(req: EditRequest) -> GenerateOutcome:
#     """Edit an existing form (JSON body). Superseded by /form-ai/assist (with base_json)."""
#     if not req.prompt.strip():
#         raise HTTPException(status_code=422, detail="prompt is required")
#     if not isinstance(req.base_json, dict) or "sections" not in req.base_json:
#         raise HTTPException(
#             status_code=422,
#             detail="base_json must be a form object with a 'sections' array",
#         )
#     return await _run_edit(req.base_json, req.prompt)
