"""
Dependency graph — which fields reference which.

Context selection (plan v3 §7.3, §7.7) needs to know a target field's
"neighborhood": the fields it depends on and the fields that depend on it. If a
change touches a field wired into conditional logic, the LLM must see those
related fields to reason correctly — but nothing more. This module builds a
directed graph of field-to-field references from the form, then expands a
bounded neighborhood around any target.

Edge direction: A -> B means "A references B" (B is an input/dependency of A).
We keep both directions so the neighborhood can walk upstream (what B feeds) and
downstream (who feeds A).

Reference sources (grounded in formBuilder.types.ts and the real corpus):
  - visibleWhen / hiddenWhen / disabledWhen  (BOTH the simple-map and the
    structured {operator, conditions:[{field}]} shapes)
  - dependsOn / dependsOnAny                 (id lists)
  - sourceField / endDateField              (single ids)
  - conditionalAutoFillWhen[].condition.field and .autoFill.fieldId
  - autoFillWhen.field
  - concatenateFields                        (id list)
  - formula                                  (ids inside fn(...) calls)
  - transliterationField / translationField (single ids)
  - datasource.sourceField                   (table option source)
  - the same keys at TABLE-COLUMN level

Only references that resolve to a KNOWN field id become edges; function names,
sentinels, and runtime-injected ids are naturally excluded (they aren't fields).

Pure Python: no LLM, no FastAPI.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field as dc_field
from typing import Any

from app.core.form_index import FormIndex

#: Matches identifier tokens inside a formula string, e.g. field ids and the
#: function names wrapping them. Function names are filtered out later because
#: they won't match a known field id.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Tokens that appear where an id would but aren't fields.
_NON_FIELD_TOKENS = frozenset({"form_load", "onLoad", "load", "operator", "conditions"})


@dataclass
class DependencyGraph:
    """
    Directed field-reference graph.

    `refs[a]` = set of field ids that `a` references (its dependencies).
    `dependents[b]` = set of field ids that reference `b`.
    Only field ids present in the form appear as nodes.
    """

    refs: dict[str, set[str]] = dc_field(default_factory=lambda: defaultdict(set))
    dependents: dict[str, set[str]] = dc_field(default_factory=lambda: defaultdict(set))
    nodes: set[str] = dc_field(default_factory=set)

    def add_edge(self, source: str, target: str) -> None:
        """Record that `source` references `target` (source depends on target)."""
        if source == target:
            return
        self.refs[source].add(target)
        self.dependents[target].add(source)
        self.nodes.add(source)
        self.nodes.add(target)

    def neighbors(self, node: str) -> set[str]:
        """All directly related ids (both directions)."""
        return set(self.refs.get(node, set())) | set(self.dependents.get(node, set()))


# --- Building ---------------------------------------------------------------


def build_dependency_graph(index: FormIndex) -> DependencyGraph:
    """Build the reference graph for a form from its index."""
    graph = DependencyGraph()
    known = index.all_field_ids()

    # Seed every field as a node so isolated fields still resolve.
    for fid in known:
        graph.nodes.add(fid)

    for entry in index.fields.values():
        for target in _extract_refs(entry.node, known):
            graph.add_edge(entry.id, target)

    # Column-level references: attribute the edge to the OWNING TABLE field, so
    # neighborhoods are expressed in the field-id namespace the LLM edits.
    for entry in index.columns.values():
        owner = entry.parent_field_id or entry.id
        sibling_cols = {
            e.id for e in index.columns.values()
            if e.parent_field_id == entry.parent_field_id
        }
        scope = known | sibling_cols
        for target in _extract_refs(entry.node, scope):
            # Only edge to form-level fields (sibling-column refs stay internal).
            if target in known and target != owner:
                graph.add_edge(owner, target)

    return graph


def _extract_refs(node: dict[str, Any], known: set[str]) -> set[str]:
    """Collect every field id `node` references, filtered to `known` ids."""
    refs: set[str] = set()

    def keep(ref: Any) -> None:
        if (
            isinstance(ref, str)
            and ref
            and ref not in _NON_FIELD_TOKENS
            and ref in known
        ):
            refs.add(ref)

    # condition keys (both shapes)
    for key in ("visibleWhen", "hiddenWhen", "disabledWhen"):
        for ref in _condition_refs(node.get(key)):
            keep(ref)

    # id-list keys
    for key in ("dependsOn", "dependsOnAny", "concatenateFields", "trigger_on_change"):
        val = node.get(key)
        if isinstance(val, list):
            for ref in val:
                keep(ref)

    # single-id keys
    for key in ("sourceField", "endDateField", "transliterationField",
                "translationField", "linkedField"):
        keep(node.get(key))

    # conditionalAutoFillWhen
    for rule in _as_list(node.get("conditionalAutoFillWhen")):
        if not isinstance(rule, dict):
            continue
        cond = rule.get("condition")
        if isinstance(cond, dict):
            keep(cond.get("field"))
        auto = rule.get("autoFill")
        if isinstance(auto, dict) and auto.get("source") == "formData":
            keep(auto.get("fieldId"))

    # autoFillWhen
    awn = node.get("autoFillWhen")
    if isinstance(awn, dict):
        keep(awn.get("field"))

    # datasource.sourceField (form_field datasource)
    ds = node.get("datasource")
    if isinstance(ds, dict):
        keep(ds.get("sourceField"))

    # formula: keep identifier tokens that match known field ids
    formula = node.get("formula")
    if isinstance(formula, str):
        for token in _IDENT_RE.findall(formula):
            keep(token)

    return refs


def _condition_refs(cond: Any) -> list[str]:
    """Field ids from a conditional value (simple map or operator/conditions)."""
    if not isinstance(cond, dict):
        return []
    if "conditions" in cond and isinstance(cond["conditions"], list):
        out = []
        for c in cond["conditions"]:
            if isinstance(c, dict) and isinstance(c.get("field"), str):
                out.append(c["field"])
        return out
    return [k for k in cond.keys() if k not in _NON_FIELD_TOKENS]


# --- Neighborhood -----------------------------------------------------------


@dataclass
class Neighborhood:
    """The bounded set of ids related to a target, plus whether it's oversized."""

    target_id: str
    ids: set[str]
    truncated: bool          # True if expansion hit max_size before completing
    depth_used: int


def neighborhood(
    graph: DependencyGraph,
    target_id: str,
    *,
    depth: int = 2,
    max_size: int = 25,
) -> Neighborhood:
    """
    BFS outward from `target_id` in BOTH directions up to `depth` hops.

    Returns the related id set (including the target). `truncated` is True if the
    walk exceeded `max_size` — the signal the context selector uses to fall back
    to Compact Full Form (plan v3 §7.7: correctness over token savings). The
    order guarantees closer relations are included first.
    """
    visited: set[str] = {target_id}
    frontier: deque[tuple[str, int]] = deque([(target_id, 0)])
    truncated = False
    max_depth_reached = 0

    while frontier:
        node, dist = frontier.popleft()
        max_depth_reached = max(max_depth_reached, dist)
        if dist >= depth:
            continue
        for nb in sorted(graph.neighbors(node)):
            if nb in visited:
                continue
            if len(visited) >= max_size:
                truncated = True
                break
            visited.add(nb)
            frontier.append((nb, dist + 1))
        if truncated:
            break

    return Neighborhood(
        target_id=target_id,
        ids=visited,
        truncated=truncated,
        depth_used=max_depth_reached,
    )


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []
