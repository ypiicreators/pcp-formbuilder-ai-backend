"""
Parse numbered section + pipe-table field specs from extracted document text.

Used when a DOCX/TXT/PDF upload looks like:
  1. Applicant Details
  Field Name | Type | Mandatory? | Dependency
  Applicant Full Name (English) | text | Mandatory | —
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECTION_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_SERVICE_NAME_RE = re.compile(r"^service\s*name\s*:\s*(.+)$", re.IGNORECASE)
_HEADER_HINT = re.compile(r"field\s*name|^\s*name\s*$", re.IGNORECASE)
_CROSS_FIELD_MARKER = re.compile(
    r"cross[\s-]*field\s*validators?|validators?\s*\(\s*not\s+a\s+section\s*\)",
    re.IGNORECASE,
)
_CROSS_FIELD_ID_HINT = re.compile(r"validator\s*id", re.IGNORECASE)

_EMPTY_DEP = frozenset({"—", "-", "–", "", "n/a", "na", "none"})


@dataclass(frozen=True)
class SpecField:
    name: str
    field_type: str
    dependency: str = ""
    mandatory: str = ""
    raw_line: str = ""


@dataclass(frozen=True)
class SpecSection:
    number: int
    title: str
    fields: list[SpecField] = field(default_factory=list)


@dataclass(frozen=True)
class SpecCrossFieldValidator:
    """Form-level validator rows from the doc appendix (not a numbered section)."""

    validator_id: str
    validator_type: str
    error_message: str
    expression: str
    raw_line: str = ""


@dataclass(frozen=True)
class ParsedFieldSpec:
    """Canonical parse result: optional service metadata + numbered sections."""

    sections: list[SpecSection]
    service_name: str | None = None
    cross_field_validators: list[SpecCrossFieldValidator] = field(default_factory=list)


def _norm_header_cell(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _map_header_columns(parts: list[str]) -> dict[str, int] | None:
    """Return role -> column index when the row looks like a table header."""
    if not parts or "|" not in " ".join(parts):
        return None
    lowered = [_norm_header_cell(p) for p in parts]
    if not any("fieldname" in c or c == "name" for c in lowered):
        if not any("type" in c for c in lowered):
            return None
    mapping: dict[str, int] = {}
    for i, cell in enumerate(lowered):
        if "fieldname" in cell or cell == "name":
            mapping["name"] = i
        elif "type" in cell or cell == "fieldtype":
            mapping["type"] = i
        elif "mandatory" in cell or "required" in cell:
            mapping["mandatory"] = i
        elif "dependency" in cell or "depend" in cell:
            mapping["dependency"] = i
        elif "fieldrules" in cell or ("rules" in cell and "logic" in cell):
            mapping["dependency"] = i
        elif "validation" in cell:
            mapping["validation"] = i
    if "name" not in mapping or "type" not in mapping:
        return None
    return mapping


def _map_cross_field_header(parts: list[str]) -> dict[str, int] | None:
    lowered = [_norm_header_cell(p) for p in parts]
    if not any("validatorid" in c or c == "id" for c in lowered):
        return None
    mapping: dict[str, int] = {}
    for i, cell in enumerate(lowered):
        if "validatorid" in cell or cell == "id":
            mapping["id"] = i
        elif cell == "type" or "validatortype" in cell:
            mapping["type"] = i
        elif "error" in cell or "message" in cell:
            mapping["message"] = i
        elif "expression" in cell or "expr" in cell:
            mapping["expression"] = i
    if "id" not in mapping or "expression" not in mapping:
        return None
    return mapping


def _default_cross_field_map(column_count: int) -> dict[str, int]:
    if column_count >= 4:
        return {"id": 0, "type": 1, "message": 2, "expression": 3}
    return {"id": 0, "type": 1, "message": 2, "expression": 3}


def _row_from_cross_field_parts(
    parts: list[str],
    column_map: dict[str, int],
    *,
    raw_line: str,
) -> SpecCrossFieldValidator | None:
    vid = _cell(parts, column_map.get("id", 0))
    if not vid or _CROSS_FIELD_ID_HINT.search(vid):
        return None
    vtype = _cell(parts, column_map.get("type", 1)) or "custom"
    message = _cell(parts, column_map.get("message", 2))
    expression = _cell(parts, column_map.get("expression", 3))
    if not expression:
        return None
    return SpecCrossFieldValidator(
        validator_id=vid,
        validator_type=vtype.strip().lower() or "custom",
        error_message=message,
        expression=expression,
        raw_line=raw_line,
    )


def _default_column_map(column_count: int) -> dict[str, int]:
    if column_count >= 4:
        return {"name": 0, "type": 1, "mandatory": 2, "dependency": 3}
    if column_count == 3:
        return {"name": 0, "type": 1, "dependency": 2}
    return {"name": 0, "type": 1}


def _looks_like_mandatory_cell(value: str) -> bool:
    low = (value or "").strip().lower()
    if not low or low in _EMPTY_DEP:
        return False
    if low in ("yes", "no", "y", "n"):
        return False
    return any(
        tok in low
        for tok in (
            "mandatory",
            "non-mandatory",
            "non mandatory",
            "optional",
            "required",
        )
    )


def _refine_three_column_map(parts: list[str], base: dict[str, int]) -> dict[str, int]:
    """When col 2 is Mandatory? and col 3 is dependency, upgrade the map."""
    if len(parts) < 3:
        return base
    if "mandatory" in base and "dependency" in base:
        return base
    if _looks_like_mandatory_cell(parts[2]):
        return {"name": 0, "type": 1, "mandatory": 2, "dependency": 3 if len(parts) > 3 else -1}
    return base


def _cell(parts: list[str], index: int) -> str:
    if index < 0 or index >= len(parts):
        return ""
    return parts[index].strip()


def _row_from_parts(
    parts: list[str],
    column_map: dict[str, int],
    *,
    raw_line: str,
) -> SpecField | None:
    name = _cell(parts, column_map.get("name", 0))
    ftype = _cell(parts, column_map.get("type", 1))
    if not name or not ftype:
        return None
    mandatory = _cell(parts, column_map["mandatory"]) if "mandatory" in column_map else ""
    dep_idx = column_map.get("dependency", 2 if len(parts) == 3 else 3)
    dependency = _cell(parts, dep_idx)
    return SpecField(
        name=name,
        field_type=ftype,
        mandatory=mandatory,
        dependency=dependency,
        raw_line=raw_line,
    )


def parse_field_spec_document(text: str) -> ParsedFieldSpec | None:
    """
    Return sections (and optional service name) when text matches the numbered table pattern.

    Returns None when the pattern is not detected (caller falls back to one-shot LLM).
    """
    if not (text or "").strip():
        return None

    service_name: str | None = None
    sections: list[SpecSection] = []
    cross_field_validators: list[SpecCrossFieldValidator] = []
    current: SpecSection | None = None
    column_map: dict[str, int] | None = None
    cross_field_mode = False
    cross_column_map: dict[str, int] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        svc = _SERVICE_NAME_RE.match(line)
        if svc:
            service_name = svc.group(1).strip()
            continue

        if _CROSS_FIELD_MARKER.search(line) and "|" not in line:
            cross_field_mode = True
            cross_column_map = None
            continue

        sec_match = _SECTION_RE.match(line)
        if sec_match:
            if cross_field_mode:
                cross_field_mode = False
                cross_column_map = None
            if current is not None:
                sections.append(current)
            current = SpecSection(
                number=int(sec_match.group(1)),
                title=sec_match.group(2).strip(),
            )
            column_map = None
            continue

        if cross_field_mode:
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue
            cf_header = _map_cross_field_header(parts)
            if cf_header is not None or (
                _CROSS_FIELD_ID_HINT.search(parts[0])
                and any("expression" in _norm_header_cell(p) for p in parts)
            ):
                cross_column_map = cf_header or _default_cross_field_map(len(parts))
                continue
            cf_map = cross_column_map or _default_cross_field_map(len(parts))
            cf_row = _row_from_cross_field_parts(parts, cf_map, raw_line=line)
            if cf_row is not None:
                cross_field_validators.append(cf_row)
            continue

        if current is None:
            continue

        if "|" not in line:
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue

        cf_inline = _map_cross_field_header(parts)
        if cf_inline is not None:
            cross_field_mode = True
            cross_column_map = cf_inline
            continue

        header_map = _map_header_columns(parts)
        if header_map is not None or (
            _HEADER_HINT.search(parts[0]) and any("type" in _norm_header_cell(p) for p in parts)
        ):
            column_map = header_map or _default_column_map(len(parts))
            continue

        cmap = column_map or _default_column_map(len(parts))
        if len(parts) == 3 and "mandatory" not in cmap:
            cmap = _refine_three_column_map(parts, cmap)

        row = _row_from_parts(parts, cmap, raw_line=line)
        if row is None:
            continue
        current.fields.append(row)

    if current is not None:
        sections.append(current)

    if len(sections) < 2:
        return None

    total_fields = sum(len(s.fields) for s in sections)
    if total_fields < 3:
        return None

    return ParsedFieldSpec(
        sections=sections,
        service_name=service_name,
        cross_field_validators=cross_field_validators,
    )


def parse_numbered_field_spec(text: str) -> list[SpecSection] | None:
    """Backward-compatible entry: sections only."""
    parsed = parse_field_spec_document(text)
    if parsed is None:
        return None
    return parsed.sections


def spec_summary_for_prompt(sections: list[SpecSection]) -> str:
    """Compact checklist the model must satisfy (section count + titles)."""
    lines = [f"Document defines {len(sections)} section(s) — implement ALL:"]
    for s in sections:
        lines.append(f"  {s.number}. {s.title} ({len(s.fields)} field row(s))")
    return "\n".join(lines)