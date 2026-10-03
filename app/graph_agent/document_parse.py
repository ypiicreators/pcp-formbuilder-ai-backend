"""Extract a WorkflowSpec from uploaded FRS / diagram documents."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.graph_agent.models import GraphAgentMessageRequest
from app.graph_agent.parse import _loads_json, graph_context_prompt, spec_from_llm_json
from app.graph_agent.prompts import (
    GRAPH_AGENT_DOCUMENT_SUPPLEMENT,
    GRAPH_AGENT_SYSTEM_PROMPT,
)
from app.graph_agent.spec import WorkflowSpec
from app.orchestration.document_flow import prepare_document_upload
from app.providers.base import Attachment, LLMError

logger = logging.getLogger(__name__)

DEFAULT_DOCUMENT_INSTRUCTION = (
    "Extract the complete officer approval workflow graph from the swimlane diagram in the attached document:\n"
    "1. SWIMLANE ROLES: The rows/swimlanes define the authorized officer roles taking action: "
    "Dealing Clerk, Sarpanch / Manbardar / MC / Patwari, Tehsildar / Naib Tehsildar, Sewa Kendra Operator, and Applicant.\n"
    "2. STATUS CARDS: Extract distinct lifecycle stages:\n"
    "- Initial Scrutiny: name: 'Assigned for application verification' (REUSE the existing initial card if present on canvas; DO NOT rename this card to an action name like 'Resubmit').\n"
    "- Field Verification: name: 'Sent for Field Verification' (Phase: Field Verification, SLA: 48 hours).\n"
    "- Approval: name: 'Pending Approval' (Phase: Approval, SLA: 24 hours).\n"
    "- Positive Terminal: name: 'Certificate Issued' (is_final: true, Phase: Delivery, SLA: 24 hours).\n"
    "- Negative Terminal: name: 'Application Rejected' (is_final: true, Phase: Delivery, SLA: 24 hours).\n"
    "- Clarification: name: 'Clarification Required' (Phase: Clarification, SLA: 24 hours).\n"
    "3. TRANSITIONS & ACTIONS (connect cards directly following diagram flow):\n"
    "- From 'Assigned for application verification' -> to 'Sent for Field Verification' (Action: 'Forward for Field Verification', Role: Dealing Clerk, SLA: 24 hours).\n"
    "- From 'Assigned for application verification' -> to 'Pending Approval' (Action: 'Forward with Deficiency', Role: Dealing Clerk, SLA: 24 hours).\n"
    "- From 'Sent for Field Verification' -> to 'Pending Approval' (Action: 'Forward with Verification Report', Role: Patwari, SLA: 48 hours).\n"
    "- From 'Pending Approval' -> to 'Certificate Issued' (Action: 'Approve & Issue Certificate', Role: Tehsildar, Naib Tehsildar, SLA: 24 hours).\n"
    "- From 'Pending Approval' -> to 'Application Rejected' (Action: 'Reject Application', Role: Tehsildar, Naib Tehsildar, SLA: 24 hours).\n"
    "- From 'Pending Approval' -> to 'Clarification Required' (Action: 'Send Back for Clarification', Role: Tehsildar, Naib Tehsildar, SLA: 24 hours).\n"
    "- From 'Clarification Required' -> to 'Assigned for application verification' (Action: 'Resubmit Application', Role: Sewa Kendra Operator, Applicant, SLA: 24 hours).\n"
    "4. CRITICAL RULES:\n"
    "- Each connection's from_ref MUST be the specific card that initiates that action (e.g. from 'Sent for Field Verification' or 'Pending Approval'), NOT the initial card for all of them!\n"
    "- 'Resubmit Application' is an ACTION on a connection from 'Clarification Required' back to 'Assigned for application verification', NEVER the title of a card.\n"
    "- Output ONLY a valid JSON object starting with { and ending with }. Absolutely NO conversational text, markdown explanations, preambles, or postscripts."
)


def _document_system_prompt() -> str:
    return f"{GRAPH_AGENT_SYSTEM_PROMPT}\n\n{GRAPH_AGENT_DOCUMENT_SUPPLEMENT}"


def _instruction(req: GraphAgentMessageRequest) -> str:
    stripped = (req.text or "").strip()
    if stripped:
        return f"{DEFAULT_DOCUMENT_INSTRUCTION}\n\nAdditional user instruction: {stripped}"
    return DEFAULT_DOCUMENT_INSTRUCTION


def _user_prompt_text(
    req: GraphAgentMessageRequest,
    *,
    extracted_text: str | None = None,
    attachment: bool = False,
) -> str:
    base = graph_context_prompt(req)
    # Chat prompt ends with "User request:\n{req.text}\n" — replace with doc instruction.
    instruction_block = f"Workflow extraction instruction:\n{_instruction(req)}\n"
    target = f"User request:\n{req.text}\n"
    if target in base:
        base = base.replace(target, instruction_block)
    else:
        base = re.sub(r"User request:\s*.*$", instruction_block, base, flags=re.DOTALL)

    if extracted_text is not None:
        return (
            f"{base}\n--- Extracted document text ---\n{extracted_text}\n\n"
            "Output ONLY the JSON workflow specification (starting with { and ending with })."
        )
    if attachment:
        return (
            f"{base}\nThe workflow document is attached as a file. "
            "Locate the workflow diagram in the document and output ONLY the JSON workflow specification (starting with { and ending with }).\n"
        )
    return base


def _robust_json_loads(text: str) -> dict[str, Any] | None:
    """Extract and parse a JSON dictionary from LLM output, handling markdown fences and minor quirks."""
    if not text or not text.strip():
        return None
    s = text.strip()

    # 1. Standard parse first
    parsed = _loads_json(s)
    if isinstance(parsed, dict):
        return parsed

    # 2. Extract outermost { ... }
    first_brace = s.find("{")
    last_brace = s.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = s[first_brace : last_brace + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        # 3. Strip single-line comments and trailing commas before closing braces/brackets
        cleaned = re.sub(r"//.*$", "", candidate, flags=re.MULTILINE)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    return None


async def parse_workflow_from_document(
    req: GraphAgentMessageRequest,
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
) -> tuple[WorkflowSpec | None, str | None]:
    """
    Run one LLM call with the uploaded file (or extracted DOCX text).

    Returns (spec, error_message). On success error_message is None.
    """
    prepared = prepare_document_upload(file_bytes, filename, content_type)
    if prepared.error:
        return None, prepared.error

    system = _document_system_prompt()
    attachments: list[Attachment] | None = None

    from app.providers.factory import get_provider

    provider = get_provider()

    if prepared.extracted_text is not None:
        user = _user_prompt_text(req, extracted_text=prepared.extracted_text)
    else:
        if not provider.supports_document_input:
            return None, (
                "The active AI provider does not support PDF or image input. "
                "Upload a Word (.docx) file, switch to the Anthropic provider, "
                "or paste the workflow description as chat text."
            )
        assert prepared.attachment is not None
        user = _user_prompt_text(req, attachment=True)
        attachments = [prepared.attachment]

    try:
        raw = await provider.generate(system, user, attachments=attachments)
    except LLMError as exc:
        logger.warning("Document workflow parse LLM error: %s", exc)
        return None, f"Could not read the document: {exc}"

    data = _robust_json_loads(raw)
    if not data:
        logger.warning(
            "Document workflow parse failed to decode JSON. Raw LLM response (%d chars): %s",
            len(raw) if raw else 0,
            (raw[:500] if raw else "EMPTY"),
        )
        return None, (
            "The AI could not extract a workflow from that document. "
            "Try a clearer diagram or add a short instruction in the message box."
        )

    spec = spec_from_llm_json(data, req.graph.nodes)
    if not spec.steps and not spec.transitions:
        logger.warning(
            "Document workflow parse produced empty spec. Raw LLM response: %s",
            (raw[:500] if raw else "EMPTY"),
        )
        return None, (
            "No status cards or connections were found in the document. "
            "Check that it includes a workflow or process diagram section."
        )
    return spec, None
