"""
Target resolver — turn an admin's instruction into the field(s) it means.

The single most important rule of context selection (plan v3 §7.5, §7.6): NEVER
guess. If "make mobile number mandatory" matches three different mobile fields,
we must ASK the admin, not silently edit the first one. This module resolves a
prompt to one of four outcomes:

  UNIQUE     — exactly one clear best match; proceed.
  AMBIGUOUS  — several comparable matches; return them for a clarification
               question (§7.5). No edit happens.
  ALL        — the admin explicitly said "all" (e.g. "all mobile number fields");
               every match is a target.
  NONE       — nothing matched deterministically; the caller falls back to
               Compact Full Form and lets the LLM locate the target.

Matching is deterministic and id-anchored: we score each field by how well the
prompt overlaps its id tokens and its English label. This is a heuristic pre-
filter, not the final authority — NONE hands off to the LLM with full context.

Pure Python: no LLM, no FastAPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from enum import Enum

from app.core.form_index import FieldEntry, FormIndex

# --- Tokenization -----------------------------------------------------------

#: Words that carry no targeting signal — dropped before matching.
_STOPWORDS = frozenset({
    "make", "set", "change", "update", "the", "a", "an", "to", "of", "for",
    "field", "fields", "please", "mandatory", "required", "optional", "add",
    "remove", "this", "that", "is", "be", "as", "it", "and", "or", "with",
    "value", "values", "option", "options", "label", "when", "if", "hidden",
    "visible", "show", "hide", "enable", "disable", "readonly", "not",
})

#: Phrases that signal the admin wants EVERY match.
_ALL_INTENT_RE = re.compile(
    r"\ball\b.*\b(field|fields|of them|matching)\b|\ball\b\s+\w+\s+(field|fields)",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens, splitting camelCase / snake_case boundaries."""
    # break camelCase: insert space before caps
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return _TOKEN_RE.findall(spaced.lower())


def _content_tokens(text: str) -> set[str]:
    """Tokens with stopwords removed — the meaningful targeting terms."""
    return {t for t in _tokens(text) if t not in _STOPWORDS and len(t) > 1}


# --- Candidate scoring ------------------------------------------------------


@dataclass
class Candidate:
    """A field that plausibly matches the prompt, with its match score."""

    entry: FieldEntry
    score: float
    covered_count: int = 0

    @property
    def field_id(self) -> str:
        return self.entry.id


@dataclass
class _ScoreParts:
    """The components behind a field's match score (used for tie-breaking)."""

    covered_count: int   # how many prompt terms the field covers (id or label)
    score: float         # the weighted score


def _score_field_parts(entry: FieldEntry, prompt_terms: set[str]) -> _ScoreParts:
    """
    Score how well a field matches the prompt terms.

    Combines id-token overlap and label-token overlap. Exact matches are boosted
    so a precise instruction wins cleanly. `covered_count` (how many prompt terms
    the field accounts for) is the primary tie-breaker: a scoped instruction like
    "applicant mobile number" covers one more term for `applicant_mobile` than for
    the sibling mobile fields, which makes it the unique winner (plan v3 §7.5).
    """
    if not prompt_terms:
        return _ScoreParts(0, 0.0)

    id_terms = set(_tokens(entry.id))
    label_terms = _content_tokens(_label_en(entry))

    covered = prompt_terms & (id_terms | label_terms)
    if not covered:
        return _ScoreParts(0, 0.0)

    coverage = len(covered) / len(prompt_terms)
    score = coverage
    if label_terms and label_terms == prompt_terms:
        score += 1.0            # prompt terms exactly equal the label terms
    if prompt_terms <= id_terms:
        score += 0.5            # every prompt term appears in the id
    if prompt_terms <= label_terms:
        score += 0.5            # every prompt term appears in the label

    return _ScoreParts(len(covered), score)


def _score_field(entry: FieldEntry, prompt_terms: set[str]) -> float:
    """Backwards-compatible scalar score."""
    return _score_field_parts(entry, prompt_terms).score


# --- Resolution -------------------------------------------------------------


class Outcome(str, Enum):
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    ALL = "all"
    NONE = "none"


@dataclass
class Resolution:
    """Result of resolving a prompt against a form."""

    outcome: Outcome
    candidates: list[Candidate] = dc_field(default_factory=list)

    @property
    def target(self) -> FieldEntry | None:
        """The single resolved field, when the outcome is UNIQUE."""
        if self.outcome is Outcome.UNIQUE and self.candidates:
            return self.candidates[0].entry
        return None

    @property
    def targets(self) -> list[FieldEntry]:
        """All resolved fields, when the outcome is ALL."""
        return [c.entry for c in self.candidates] if self.outcome is Outcome.ALL else []


#: A candidate must score at least this to be considered a real match.
MIN_SCORE = 0.5


def resolve_target(index: FormIndex, prompt: str) -> Resolution:
    """
    Resolve `prompt` to a target field (or the clarification set).

    Decision logic (correctness-first, plan v3 §7.5, §7.6):
      0. EXACT ID MATCH (highest priority): if the prompt literally contains a
         token equal to a field id (e.g. "make mobile_number required"), resolve
         directly to that field. An id is unique by definition, so naming one is
         the most precise instruction possible and must win over fuzzy label
         matching. Multiple distinct ids named -> ALL.
      1. Otherwise score every field by id/label term overlap; keep >= MIN_SCORE.
      2. NONE if nothing matches.
      3. ALL if the prompt has explicit "all ... fields" intent.
      4. Otherwise the winner must DOMINATE on term coverage; ties -> AMBIGUOUS.
    """
    # --- Step 0: exact field-id mention -----------------------------------
    id_hits = _exact_id_hits(index, prompt)
    if len(id_hits) == 1:
        return Resolution(
            outcome=Outcome.UNIQUE,
            candidates=[Candidate(entry=id_hits[0], score=99.0, covered_count=99)],
        )
    if len(id_hits) > 1:
        # The admin named several concrete ids -> treat all as targets.
        return Resolution(
            outcome=Outcome.ALL,
            candidates=[Candidate(entry=e, score=99.0, covered_count=99) for e in id_hits],
        )

    prompt_terms = _content_tokens(prompt)
    scored: list[Candidate] = []
    for e in index.fields.values():
        parts = _score_field_parts(e, prompt_terms)
        if parts.score >= MIN_SCORE:
            scored.append(Candidate(entry=e, score=parts.score,
                                    covered_count=parts.covered_count))

    matches = sorted(scored, key=lambda c: (c.covered_count, c.score), reverse=True)
    if not matches:
        return Resolution(outcome=Outcome.NONE)

    if _ALL_INTENT_RE.search(prompt):
        # Every match sharing the top coverage tier is a target.
        top_cov = matches[0].covered_count
        return Resolution(
            outcome=Outcome.ALL,
            candidates=[c for c in matches if c.covered_count == top_cov],
        )

    # Candidates tied at the maximum term coverage are the contenders.
    top_cov = matches[0].covered_count
    contenders = [c for c in matches if c.covered_count == top_cov]

    if len(contenders) == 1:
        return Resolution(outcome=Outcome.UNIQUE, candidates=[contenders[0]])

    # SAFETY (plan v3 §7.5): if two or more contenders have a label that EXACTLY
    # equals the prompt terms, they are semantically indistinguishable to the
    # admin regardless of id-token scoring quirks. Never let an incidental id
    # boost silently pick one — ask.
    prompt_key = frozenset(prompt_terms)
    exact_label = [
        c for c in contenders
        if frozenset(_content_tokens(_label_en(c.entry))) == prompt_key
    ]
    if len(exact_label) >= 2:
        return Resolution(outcome=Outcome.AMBIGUOUS, candidates=exact_label)

    # Otherwise break the tie by weighted score (exact-match boosts).
    top_score = contenders[0].score
    best_by_score = [c for c in contenders if abs(c.score - top_score) < 1e-9]
    if len(best_by_score) == 1:
        return Resolution(outcome=Outcome.UNIQUE, candidates=[best_by_score[0]])

    # Genuine tie => ask the admin.
    return Resolution(outcome=Outcome.AMBIGUOUS, candidates=best_by_score)


# --- Helpers ----------------------------------------------------------------


#: Identifier-like tokens as they appear verbatim (keep _ and - and digits),
#: so a field id such as "mobile_number" or "parent_mobile_no" is matched whole.
_ID_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")


def _exact_id_hits(index: FormIndex, prompt: str) -> list[FieldEntry]:
    """
    Return field entries whose id appears VERBATIM as a token in the prompt.

    Deterministic and exact: an id is globally unique, so a verbatim mention is
    an unambiguous target. Preserves prompt order and de-duplicates. Column ids
    are intentionally excluded (they aren't in the field-edit namespace).
    """
    tokens = set(_ID_TOKEN_RE.findall(prompt))
    if not tokens:
        return []
    hits: list[FieldEntry] = []
    seen: set[str] = set()
    for fid in index.all_field_ids():
        if fid in tokens and fid not in seen:
            entry = index.get_field(fid)
            if entry is not None:
                hits.append(entry)
                seen.add(fid)
    return hits


def _label_en(entry: FieldEntry) -> str:
    label = entry.node.get("label")
    if isinstance(label, dict) and isinstance(label.get("en"), str):
        return label["en"]
    return ""


def candidate_options(resolution: Resolution) -> list[dict[str, str]]:
    """
    Shape the ambiguous candidates for the ClarificationResponse.

    Each entry gives the unique field id, the display label, and the section
    path so the admin can tell duplicates apart (plan v3 §7.5).
    """
    return [
        {
            "field_id": c.entry.id,
            "label": _label_en(c.entry) or c.entry.id,
            "section_path": c.entry.path,
        }
        for c in resolution.candidates
    ]
