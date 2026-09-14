"""
Validation entry point — runs L1 (structural) then L2 (business) and returns
one aggregated result.

This is the gate the orchestration layer calls after generating or applying a
change-set. Both levels run every time so the caller sees the complete error
list (the repair step wants ALL errors, not just the first). Building the form
index once here keeps traversal cost down (plan §4.5).
"""

from __future__ import annotations

from typing import Any

from app.core.form_index import FormIndex, build_index
from app.validation.business_rules import validate_business
from app.validation.result import ValidationResult
from app.validation.schema import validate_structural


def validate_form(form: dict[str, Any]) -> ValidationResult:
    """Validate a complete form object. Builds the index internally."""
    index = build_index(form)
    return validate_index(index)


def validate_index(index: FormIndex) -> ValidationResult:
    """Validate a pre-built index (avoids re-indexing when the caller has one)."""
    result = ValidationResult()
    result.extend(validate_structural(index))
    result.extend(validate_business(index))
    return result
