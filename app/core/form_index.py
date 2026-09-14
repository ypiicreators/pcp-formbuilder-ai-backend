"""
Form index — the foundation for every other deterministic operation.

Every downstream piece (target resolution, dependency graph, validation,
change-set application, context selection) needs one shared ability: given a
form JSON, find any field/column by its id and know exactly where it lives.
This module provides that, and nothing else. It is PURE: no LLM, no FastAPI,
no network — just functions over dicts.

Grounding (verified against `pcp-admin-portal/src/serviceJson/*.json` and
`formBuilder.types.ts`):

  form
    └── sections: [ Section ]
          ├── fields: [ Field ]                      # most common
          └── subSections: [ SubSection ]            # allowed by the type,
                └── fields: [ Field ]                #   absent in current corpus
          Field of type table/computed_table/verification:
            └── columns: [ TableColumn ]             # columns ARE addressable ids

`generateJSON()` emits a section as EITHER `fields` OR `subSections` (never
both), but we traverse both if present so we never miss an id.

The index keys on **id**. Field ids are meant to be globally unique across the
whole form (plan business rule); table COLUMN ids are only unique within their
table, so we track them separately and never merge the two namespaces blindly.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Iterator


class NodeKind(str, Enum):
    """What kind of addressable node an index entry points at."""

    FIELD = "field"            # a field directly under a section
    SUBSECTION_FIELD = "subsection_field"  # a field inside a subSection
    COLUMN = "column"          # a column inside a table/computed_table/verification


#: Field types whose `columns[]` contain addressable child nodes.
COLUMN_BEARING_TYPES = frozenset({"table", "computed_table", "verification"})


@dataclass
class FieldEntry:
    """
    One addressable node in the form, plus where it lives.

    `node` is the live dict from the form (not a copy) so callers that need to
    mutate — e.g. the change-set applier — can edit in place. `path` is a
    human-readable trail used for clarification prompts and error messages.
    """

    id: str
    kind: NodeKind
    node: dict[str, Any]
    #: Section id this node ultimately belongs to.
    section_id: str
    #: For columns: the id of the owning table field. None otherwise.
    parent_field_id: str | None = None
    #: Readable trail, e.g. "Applicant Details > owner_table > Name".
    path: str = ""


@dataclass
class FormIndex:
    """
    The result of indexing a form.

    `fields` maps field id -> FieldEntry for section-level and subSection-level
    fields (the globally-unique namespace). `columns` maps
    "<table_id>.<column_id>" -> FieldEntry, keeping column ids scoped to their
    table so identical column ids in different tables never collide.
    `duplicate_field_ids` records any field id seen more than once — a business
    rule violation the validator will report.
    """

    fields: dict[str, FieldEntry] = dc_field(default_factory=dict)
    columns: dict[str, FieldEntry] = dc_field(default_factory=dict)
    duplicate_field_ids: list[str] = dc_field(default_factory=list)

    # --- Lookups ----------------------------------------------------------

    def get_field(self, field_id: str) -> FieldEntry | None:
        """Return the entry for a section/subSection field id, or None."""
        return self.fields.get(field_id)

    def get_column(self, table_id: str, column_id: str) -> FieldEntry | None:
        """Return the entry for a table column, or None."""
        return self.columns.get(f"{table_id}.{column_id}")

    def has_id(self, field_id: str) -> bool:
        """True if `field_id` names a section/subSection field."""
        return field_id in self.fields

    def all_field_ids(self) -> set[str]:
        """Every section/subSection field id (the reference-target namespace)."""
        return set(self.fields.keys())

    def find_by_label(self, label_en: str) -> list[FieldEntry]:
        """
        Return every field whose English label matches `label_en` (case- and
        whitespace-insensitive). Used by target resolution to detect ambiguity
        (e.g. three "Mobile Number" fields) — never to auto-pick the first.
        """
        needle = _norm(label_en)
        out: list[FieldEntry] = []
        for entry in self.fields.values():
            if _norm(_label_en(entry.node)) == needle:
                out.append(entry)
        return out

    def __iter__(self) -> Iterator[FieldEntry]:
        """Iterate all field entries (not columns)."""
        return iter(self.fields.values())


# --- Building the index -----------------------------------------------------


def build_index(form: dict[str, Any]) -> FormIndex:
    """
    Walk `form` and index every addressable node by id.

    Traverses: sections -> fields, sections -> subSections -> fields, and for
    column-bearing field types, field -> columns. Records duplicate field ids
    rather than raising, so the caller can surface them as validation errors.
    """
    index = FormIndex()
    sections = form.get("sections")
    if not isinstance(sections, list):
        return index

    for section in sections:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("id", ""))
        section_title = _label_en(section) or section_id

        # A section carries fields OR subSections (generateJSON emits one),
        # but we handle both defensively.
        for fld in _as_list(section.get("fields")):
            _index_field(index, fld, section_id, section_title, NodeKind.FIELD)

        for sub in _as_list(section.get("subSections")):
            if not isinstance(sub, dict):
                continue
            sub_title = _label_en(sub) or str(sub.get("id", ""))
            trail = f"{section_title} > {sub_title}"
            for fld in _as_list(sub.get("fields")):
                _index_field(
                    index, fld, section_id, trail, NodeKind.SUBSECTION_FIELD
                )

    return index


def _index_field(
    index: FormIndex,
    fld: Any,
    section_id: str,
    trail: str,
    kind: NodeKind,
) -> None:
    """Index a single field and, if it bears columns, its columns too."""
    if not isinstance(fld, dict):
        return
    field_id = fld.get("id")
    if not isinstance(field_id, str) or not field_id:
        return

    entry = FieldEntry(
        id=field_id,
        kind=kind,
        node=fld,
        section_id=section_id,
        path=f"{trail} > {field_id}",
    )

    if field_id in index.fields:
        # Seen before -> business-rule duplicate. Keep the first occurrence as
        # canonical; record the id once for reporting.
        if field_id not in index.duplicate_field_ids:
            index.duplicate_field_ids.append(field_id)
    else:
        index.fields[field_id] = entry

    # Index columns for table-like fields.
    if fld.get("type") in COLUMN_BEARING_TYPES:
        for col in _as_list(fld.get("columns")):
            if not isinstance(col, dict):
                continue
            col_id = col.get("id")
            if not isinstance(col_id, str) or not col_id:
                continue
            index.columns[f"{field_id}.{col_id}"] = FieldEntry(
                id=col_id,
                kind=NodeKind.COLUMN,
                node=col,
                section_id=section_id,
                parent_field_id=field_id,
                path=f"{trail} > {field_id} > {col_id}",
            )


# --- Small helpers ----------------------------------------------------------


def _as_list(value: Any) -> list:
    """Return `value` if it's a list, else an empty list."""
    return value if isinstance(value, list) else []


def _label_en(node: dict[str, Any]) -> str:
    """Extract the English label/title text from a node, or ''."""
    for key in ("label", "title"):
        loc = node.get(key)
        if isinstance(loc, dict):
            en = loc.get("en")
            if isinstance(en, str):
                return en
    return ""


def _norm(text: str) -> str:
    """Normalize label text for matching: lowercase, collapse whitespace."""
    return " ".join(text.lower().split())
