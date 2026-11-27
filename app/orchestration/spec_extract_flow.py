"""
Turn an arbitrary document into the same ParsedFieldSpec the table parsers emit.

Used when the upload is not a known pipe-table layout. The model returns a small
JSON spec (titles + field names); the existing skeleton + enrich path then builds
the form so section count and field names stay faithful to the document.
"""

from __future__ import annotations

import logging
import time

from app.core.change_set import _loads_tolerant
from app.core.doc_spec_parser import ParsedFieldSpec, spec_from_object
from app.core.doc_trace import stage
from app.prompts.spec_extract_prompts import (
    SPEC_EXTRACT_SYSTEM,
    build_spec_extract_user_prompt,
)
from app.providers.base import Attachment, LLMError, LLMProvider

logger = logging.getLogger("app.orchestration.spec_extract_flow")


async def extract_spec_from_document(
    provider: LLMProvider,
    *,
    extracted_text: str | None,
    instruction: str | None,
    attachments: list[Attachment] | None = None,
) -> ParsedFieldSpec | None:
    """Ask the model for a canonical spec. Returns None if the output is unusable."""
    if not (extracted_text or "").strip() and not attachments:
        return None
    user = build_spec_extract_user_prompt(instruction, extracted_text=extracted_text)
    with stage(logger, "spec_extract", has_text=bool(extracted_text), has_file=bool(attachments)) as st:
        started = time.monotonic()
        try:
            raw = await provider.generate(
                SPEC_EXTRACT_SYSTEM, user, attachments=attachments
            )
        except LLMError as exc:
            logger.warning("spec_extract: LLM error: %s", exc)
            st["result"] = "LLM error"
            return None
        obj, err = _loads_tolerant(raw)
        if err or not isinstance(obj, dict):
            logger.warning("spec_extract: not JSON (%s)", err or "not an object")
            st["result"] = "parse failed"
            return None
        parsed = spec_from_object(obj)
        if parsed is None:
            logger.warning("spec_extract: JSON had no usable sections/fields")
            st["result"] = "empty spec"
            return None
        st["sections"] = len(parsed.sections)
        st["fields"] = sum(len(s.fields) for s in parsed.sections)
        st["took"] = f"{time.monotonic() - started:.1f}s"
        logger.info(
            "spec_extract: sections=%d fields=%d titles=%s",
            st["sections"],
            st["fields"],
            [s.title for s in parsed.sections],
        )
        return parsed
