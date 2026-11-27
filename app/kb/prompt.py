"""Format live KB entries for LLM user prompts."""

from __future__ import annotations

import json
from typing import Any

from app.kb.identity import resolve_identity
from app.kb.match import select_kb_entries


class KbPromptContext:
    """Published KB for one request. Builds a prompt block scoped to a given text,
    so each call only carries the entries that match what it is generating."""

    def __init__(self, entries: list[dict[str, Any]], *, mode: str = "document") -> None:
        self.entries = entries
        self.mode = mode

    def block_for(self, text: str) -> str:
        return build_kb_prompt_block(self.entries, text, mode=self.mode)

    def identity_for(self, label: str, field_type: str | None = None):
        """Canonical id/type pinned by a KB template for a field with this label, if any."""
        return resolve_identity(self.entries, label, field_type)


def build_kb_prompt_block(
    entries: list[dict[str, Any]],
    user_text: str,
    *,
    mode: str = "generate",
) -> str:
    """Non-empty when at least one live entry applies to this request."""
    picked = select_kb_entries(entries, user_text, mode=mode)
    if not picked:
        return ""

    lines = [
        "PUBLISHED KNOWLEDGE BASE (mandatory — overrides generic guesses):",
        "When a rule or API below applies, follow it exactly. For API-backed dropdowns",
        'use "datasource" from the entry — never invent static "options". Set',
        '"kbKey" on the field to the entry key (e.g. geo.district_api).',
        "",
    ]

    for e in picked:
        kind = e.get("kind")
        key = e.get("key")
        title = e.get("title", key)
        lines.append(f"### [{kind}] {title} (`{key}`)")
        body = (e.get("body") or "").strip()
        if body:
            lines.append(body)
        payload = e.get("payload")
        if payload is not None:
            lines.append("Payload (use exactly for datasource or field merge):")
            lines.append(json.dumps(payload, indent=2, ensure_ascii=False))
        lines.append("")

    return "\n".join(lines).strip()