"""Tests for document-mode user prompts (faithful implementation intent)."""

from app.prompts.user_prompts import (
    DEFAULT_DOC_APPEND_INSTRUCTION,
    DEFAULT_DOC_INSTRUCTION,
    build_doc_prompt,
    build_doc_prompt_text_fallback,
)


def test_default_doc_instruction_requires_full_coverage():
    assert "every section" in DEFAULT_DOC_INSTRUCTION.lower()
    assert "infer" not in DEFAULT_DOC_INSTRUCTION.lower()


def test_build_doc_prompt_fidelity_rules_not_infer():
    prompt = build_doc_prompt(None)
    assert "MODE: generate_from_document" in prompt
    assert "source of truth" in prompt
    assert "EVERY section" in prompt
    assert "certificate" not in prompt.lower()
    assert "not to transcribe" not in prompt.lower()
    assert DEFAULT_DOC_INSTRUCTION in prompt


def test_build_doc_prompt_text_fallback_includes_extracted_body():
    body = "1. Applicant Details\nField Name | Type | Dependency\nName | text | —"
    prompt = build_doc_prompt_text_fallback(None, body)
    assert "EXTRACTED DOCUMENT TEXT" in prompt
    assert body in prompt
    assert "EVERY field" in prompt


def test_append_mode_mentions_canvas_append():
    prompt = build_doc_prompt(None, append_to_existing=True)
    assert DEFAULT_DOC_APPEND_INSTRUCTION in prompt
    assert "APPEND" in prompt or "append" in prompt