"""
Context selector — decide how much of the (already-compacted) form the LLM sees.

This is the heart of the §7 enhancement. Compaction shrinks the form's SIZE;
context selection decides HOW MUCH of that compact form to send for a given
request (plan v3 §7.8). It picks the smallest context that still lets the model
edit correctly, and falls back to more context whenever correctness is at risk
(§7.7: correctness over token savings).

Four outcomes:

  CLARIFY            — the prompt matched several fields; we can't safely pick.
                       Return candidates for a clarification question (§7.5).
                       No LLM context is produced yet.
  TARGETED_FIELD     — one field resolved and it has NO dependency edges. Send
                       just that field's compact node (§7.2).
  TARGETED_DEPENDENCY— one field resolved and it participates in a dependency
                       graph. Send the field plus its bounded neighborhood (§7.3).
  COMPACT_FULL_FORM  — the request is broad, the target is unknown (NONE), spans
                       many fields (ALL), or the neighborhood is too large to
                       bound safely. Send the whole compact form (§7.4).

Whatever the level, the FULL production form stays server-side (§7.9). The
selected context is reasoning input only; the change-set the LLM returns is
applied to the original.

Pure Python: no LLM, no FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any

from app.core.compaction import _compact_field, compact_form
from app.core.dependency_graph import build_dependency_graph, neighborhood
from app.core.form_index import FormIndex, build_index
from app.core.target_resolver import (
    Outcome,
    Resolution,
    candidate_options,
    resolve_target,
)


class ContextLevel(str, Enum):
    CLARIFY = "clarify"
    TARGETED_FIELD = "targeted_field"
    TARGETED_DEPENDENCY = "targeted_dependency"
    COMPACT_FULL_FORM = "compact_full_form"


@dataclass
class ContextSelection:
    """
    The chosen context and the decision behind it.

    `context` is the compact structure to hand the LLM (None for CLARIFY).
    `target_ids` are the field ids the edit is expected to touch (empty for the
    full-form fallback). `clarification` carries candidate options when CLARIFY.
    `reason` is a short human-readable explanation for logs/telemetry.
    """

    level: ContextLevel
    context: dict[str, Any] | None = None
    target_ids: list[str] = dc_field(default_factory=list)
    clarification: list[dict[str, str]] = dc_field(default_factory=list)
    reason: str = ""


#: Neighborhood expansion settings (mirror dependency_graph defaults).
_NEIGHBORHOOD_DEPTH = 2
_NEIGHBORHOOD_MAX = 25


def select_context(form: dict[str, Any], prompt: str) -> ContextSelection:
    """
    Choose the minimal LLM context for editing `form` per `prompt`.

    Builds the index once and reuses it for resolution, dependency analysis, and
    context construction.
    """
    index = build_index(form)
    resolution = resolve_target(index, prompt)

    if resolution.outcome is Outcome.AMBIGUOUS:
        return ContextSelection(
            level=ContextLevel.CLARIFY,
            clarification=candidate_options(resolution),
            reason=f"{len(resolution.candidates)} fields match; asking the admin",
        )

    if resolution.outcome is Outcome.NONE:
        return _full_form(form, reason="no field resolved from the prompt")

    if resolution.outcome is Outcome.ALL:
        # Explicit multi-field intent: the change spans many fields, so full
        # compact form is the safe, correct context.
        ids = [c.entry.id for c in resolution.candidates]
        return _full_form(
            form,
            reason=f"'all' intent spanning {len(ids)} fields",
            target_ids=ids,
        )

    # --- UNIQUE: decide targeted-field vs targeted-dependency -------------
    target = resolution.target
    assert target is not None  # UNIQUE guarantees a target
    graph = build_dependency_graph(index)

    related = graph.neighbors(target.id)
    if not related:
        # Isolated field, no dependencies -> smallest possible context.
        return ContextSelection(
            level=ContextLevel.TARGETED_FIELD,
            context=_targeted_context(index, [target.id]),
            target_ids=[target.id],
            reason="field has no dependencies",
        )

    nb = neighborhood(
        graph, target.id, depth=_NEIGHBORHOOD_DEPTH, max_size=_NEIGHBORHOOD_MAX
    )
    if nb.truncated:
        # Dependency web too large to bound safely -> fall back (§7.7).
        return _full_form(
            form,
            reason=f"dependency neighborhood exceeded {_NEIGHBORHOOD_MAX}",
            target_ids=[target.id],
        )

    return ContextSelection(
        level=ContextLevel.TARGETED_DEPENDENCY,
        context=_targeted_context(index, sorted(nb.ids)),
        target_ids=[target.id],
        reason=f"field + {len(nb.ids) - 1} dependency-related fields",
    )


# --- Context construction ---------------------------------------------------


def _targeted_context(index: FormIndex, ids: list[str]) -> dict[str, Any]:
    """
    Build a compact context containing only `ids`, grouped by their section.

    Grouping by section preserves the structure the model needs to locate an id
    and keeps the shape identical to `compact_form` output (just filtered), so
    the prompt format is consistent across all context levels.
    """
    wanted = set(ids)
    # Preserve section order; include a section only if it holds a wanted field.
    sections_out: list[dict[str, Any]] = []
    for section in index_sections(index):
        sec_fields = [
            _compact_field(entry.node)
            for entry in section_field_entries(index, section["id"])
            if entry.id in wanted
        ]
        if sec_fields:
            sections_out.append({
                "id": section["id"],
                "title": section["title"],
                "fields": sec_fields,
            })
    return {"sections": sections_out}


def index_sections(index: FormIndex) -> list[dict[str, str]]:
    """Ordered, de-duplicated (id, title) of sections seen in the index."""
    seen: dict[str, str] = {}
    order: list[str] = []
    for entry in index.fields.values():
        if entry.section_id not in seen:
            # Title isn't on the field entry; derive from the path's first hop.
            title = entry.path.split(" > ")[0] if entry.path else entry.section_id
            seen[entry.section_id] = title
            order.append(entry.section_id)
    return [{"id": sid, "title": seen[sid]} for sid in order]


def section_field_entries(index: FormIndex, section_id: str):
    """All field entries belonging to `section_id`, in index order."""
    return [e for e in index.fields.values() if e.section_id == section_id]


def _full_form(
    form: dict[str, Any],
    *,
    reason: str,
    target_ids: list[str] | None = None,
) -> ContextSelection:
    """Fallback: the entire compact form as context."""
    return ContextSelection(
        level=ContextLevel.COMPACT_FULL_FORM,
        context=compact_form(form),
        target_ids=target_ids or [],
        reason=reason,
    )
