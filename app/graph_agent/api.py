"""HTTP API for the workflow graph agent."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.graph_agent.document_parse import parse_workflow_from_document
from app.graph_agent.graph import run_graph_agent
from app.graph_agent.models import GraphAgentMessageRequest, GraphAgentMessageResponse

router = APIRouter(prefix="/workflow-graph", tags=["workflow-graph-agent"])

_MAX_UPLOAD_BYTES = 15 * 1024 * 1024


@router.post("/message", response_model=GraphAgentMessageResponse)
async def workflow_graph_message(req: GraphAgentMessageRequest) -> GraphAgentMessageResponse:
    if not (req.text or "").strip() and not req.answers:
        raise HTTPException(status_code=422, detail="Message text is required.")
    return await run_graph_agent(req)


@router.post("/message-from-document", response_model=GraphAgentMessageResponse)
async def workflow_graph_message_from_document(
    payload: str = Form(
        ...,
        description="JSON body matching GraphAgentMessageRequest (text = optional instruction).",
    ),
    file: UploadFile = File(..., description="FRS, workflow PDF, Word doc, or flow diagram image."),
) -> GraphAgentMessageResponse:
    try:
        req = GraphAgentMessageRequest.model_validate(json.loads(payload))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload JSON: {exc}") from exc

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    if len(data) > _MAX_UPLOAD_BYTES:
        mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=422, detail=f"File too large (limit {mb} MB).")

    sid = req.session_id or str(uuid.uuid4())
    req = req.model_copy(update={"session_id": sid})

    spec, err = await parse_workflow_from_document(
        req,
        data,
        file.filename or "document",
        file.content_type,
    )
    if err or spec is None:
        return GraphAgentMessageResponse(
            status="error",
            session_id=sid,
            message=err or "Could not extract a workflow from the document.",
        )

    return await run_graph_agent(req, initial_spec=spec)
