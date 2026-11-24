"""Tests for generate-mode prompt preflight."""

from app.core.generate_prompt_gate import (
    GENERATE_CLARIFICATION_QUESTION,
    assess_generate_prompt_sufficiency,
)
from app.config import get_settings


def test_vague_prompts_blocked():
    assert assess_generate_prompt_sufficiency("let create a form") == GENERATE_CLARIFICATION_QUESTION
    assert assess_generate_prompt_sufficiency("create a form") == GENERATE_CLARIFICATION_QUESTION
    assert assess_generate_prompt_sufficiency("Create form") == GENERATE_CLARIFICATION_QUESTION


def test_specific_prompts_allowed():
    assert assess_generate_prompt_sufficiency(
        "Income certificate: applicant name, DOB, address, supporting uploads"
    ) is None
    assert assess_generate_prompt_sufficiency(
        "Form for birth certificate registration with name and date of birth"
    ) is None
    assert assess_generate_prompt_sufficiency(
        "Three sections — personal details, address, declaration"
    ) is None


def test_long_prompt_allowed_without_keywords():
    assert (
        assess_generate_prompt_sufficiency(
            "We need something for village panchayat land verification requests"
        )
        is None
    )


def test_strict_mode_disabled(monkeypatch):
    from app import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "get_settings",
        lambda: config_mod.Settings(strict_generate_prompt=False),
    )
    config_mod.get_settings.cache_clear()
    try:
        assert assess_generate_prompt_sufficiency("create a form") is None
    finally:
        config_mod.get_settings.cache_clear()