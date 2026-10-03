"""Fuzzy-match spoken names to catalog ids and graph step ids."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.graph_agent.models import CatalogItem, GraphNodeSnapshot, Question, QuestionOption

_STOP = re.compile(r"[^a-z0-9]+")


def _norm(value: str) -> str:
    return _STOP.sub(" ", (value or "").lower()).strip()


def _score(query: str, label: str) -> float:
    q, l = _norm(query), _norm(label)
    if not q or not l:
        return 0.0
    if q == l:
        return 1.0
    q_words = set(q.split())
    l_words = set(l.split())
    if q_words == l_words:
        return 1.0
    # Exact phrase containment with word boundaries
    if f" {q} " in f" {l} ":
        ratio = len(q) / len(l)
        return min(0.96, 0.85 + 0.15 * ratio)
    if f" {l} " in f" {q} ":
        ratio = len(l) / len(q)
        return min(0.96, 0.85 + 0.15 * ratio)
    # Token overlap
    common = q_words.intersection(l_words)
    if common:
        overlap_ratio = len(common) / max(len(q_words), len(l_words))
        if overlap_ratio >= 0.8:
            return 0.90
        if overlap_ratio >= 0.5:
            return 0.75
    return SequenceMatcher(None, q, l).ratio()


@dataclass
class MatchResult:
    item: CatalogItem | None
    options: list[CatalogItem]
    unique: bool


def match_catalog(query: str | None, items: list[CatalogItem], limit: int = 8) -> MatchResult:
    """Match a spoken name. Dropdown questions always get the full catalog (ranked)."""
    del limit  # kept for call-site compatibility; never truncate the dropdown
    if not items:
        return MatchResult(item=None, options=[], unique=False)

    if not (query or "").strip():
        return MatchResult(item=None, options=list(items), unique=False)

    ranked = sorted(items, key=lambda it: _score(query, it.label), reverse=True)
    best = ranked[0]
    best_score = _score(query, best.label)

    if best_score >= 0.88:
        second = _score(query, ranked[1].label) if len(ranked) > 1 else 0.0
        if best_score - second >= 0.08 or best_score >= 0.99:
            return MatchResult(item=best, options=ranked, unique=True)

    return MatchResult(item=None, options=ranked, unique=False)


def match_node(query: str | None, nodes: list[GraphNodeSnapshot]) -> MatchResult:
    items = [CatalogItem(id=n.step_id, label=n.name) for n in nodes]
    return match_catalog(query, items)


def question_from_match(
    key: str,
    field: str,
    entity: str,
    prompt: str,
    match: MatchResult,
    group_title: str | None = None,
    default_id: int | None = None,
    default_value: Any | None = None,
) -> Question:
    best_id = default_id if default_id is not None else (match.item.id if match.item else None)
    return Question(
        key=key,
        field=field,
        entity=entity,
        prompt=prompt,
        options=[QuestionOption(id=i.id, label=i.label) for i in match.options],
        group_title=group_title,
        default_id=best_id,
        default_value=default_value,
    )
