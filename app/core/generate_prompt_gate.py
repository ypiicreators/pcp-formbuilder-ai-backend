"""
Preflight checks for generate-mode (natural-language) prompts.

When strict mode is enabled, vague prompts must not reach the LLM — the API
returns a clarification question instead of inventing a default form.
"""

from __future__ import annotations

import re

from app.config import get_settings

GENERATE_CLARIFICATION_QUESTION: str = (
    "Please mention what you need to create, or define the fields you need. "
    "For example: the service or scheme name, and the sections and fields "
    "(such as applicant name, date of birth, address, uploads, or declaration)."
)

# Whole-prompt patterns that carry no field/service signal on their own.
_VAGUE_FULL_MATCH: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^create\s+(a\s+)?form[.!?\s]*$",
        r"^(let'?s|let)\s+create\s+(a\s+)?form[.!?\s]*$",
        r"^make\s+(a\s+)?form[.!?\s]*$",
        r"^build\s+(a\s+)?form[.!?\s]*$",
        r"^new\s+form[.!?\s]*$",
        r"^i\s+want\s+(to\s+)?create\s+(a\s+)?form[.!?\s]*$",
        r"^help\s+me\s+create\s+(a\s+)?form[.!?\s]*$",
        r"^form\s+please[.!?\s]*$",
        r"^hi[.!?\s]*$",
        r"^hello[.!?\s]*$",
        r"^help[.!?\s]*$",
    )
)

# Any one of these suggests the admin named a service, field, or structure.
_SUBSTANCE_KEYWORDS: frozenset[str] = frozenset(
    {
        "section",
        "sections",
        "field",
        "fields",
        "add",
        "include",
        "require",
        "mandatory",
        "name",
        "dob",
        "birth",
        "mobile",
        "phone",
        "email",
        "address",
        "district",
        "pincode",
        "pin",
        "aadhaar",
        "aadhar",
        "pan",
        "upload",
        "document",
        "file",
        "certificate",
        "application",
        "license",
        "licence",
        "scheme",
        "service",
        "permit",
        "registration",
        "table",
        "dropdown",
        "select",
        "radio",
        "checkbox",
        "declaration",
        "income",
        "property",
        "officer",
        "workflow",
        "status",
        "approval",
    }
)

# "form for X" / "application for X" style intent.
_FOR_PATTERN = re.compile(
    r"\b(form|application)\s+for\b|\bfor\s+(applying|obtaining|getting)\b",
    re.IGNORECASE,
)


def assess_generate_prompt_sufficiency(prompt: str) -> str | None:
    """
    Return a clarification question when the prompt is too vague to generate.

    Returns None when generation should proceed (or when strict mode is off).
    """
    if not get_settings().strict_generate_prompt:
        return None

    text = (prompt or "").strip()
    if not text:
        return None

    if any(pat.match(text) for pat in _VAGUE_FULL_MATCH):
        return GENERATE_CLARIFICATION_QUESTION

    # Longer free text is usually intentional even without keywords.
    if len(text) >= 48:
        return None

    lower = text.lower()

    if _FOR_PATTERN.search(text):
        return None

    if any(kw in lower for kw in _SUBSTANCE_KEYWORDS):
        return None

    # List-like structure: "name, dob, address" or numbered / bulleted lists.
    if "," in text and len([p for p in text.split(",") if p.strip()]) >= 2:
        return None
    if re.search(r"(?m)^\s*(\d+[.)]|[-*•])\s+\S", text):
        return None

    # Very short utterances with no substance.
    if len(text) < 28 and len(text.split()) <= 5:
        return GENERATE_CLARIFICATION_QUESTION

    # Short but still no keywords — ask for detail rather than hallucinate.
    if len(text) < 48:
        return GENERATE_CLARIFICATION_QUESTION

    return None