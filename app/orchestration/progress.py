"""User-facing SSE progress payloads for document-to-form generation."""

from __future__ import annotations

from typing import Any


def event(phase: str, message: str, **extra: Any) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {"phase": phase, "message": message}
    payload.update(extra)
    return ("progress", payload)


def creating_section(index: int, total: int, title: str) -> tuple[str, dict[str, Any]]:
    name = title.strip() or f"section {index}"
    return event(
        "section",
        f"Creating {name} (section {index} of {total})",
        index=index,
        total=total,
        section=name,
    )


def validating_section(index: int, total: int, title: str) -> tuple[str, dict[str, Any]]:
    name = title.strip() or f"section {index}"
    return event(
        "validate",
        f"Applying validation in {name} (section {index} of {total})",
        index=index,
        total=total,
        section=name,
    )


def finished_section(index: int, total: int, title: str) -> tuple[str, dict[str, Any]]:
    name = title.strip() or f"section {index}"
    return event(
        "sections",
        f"Finished {name} ({index} of {total} sections)",
        done=index,
        total=total,
        section=name,
    )
