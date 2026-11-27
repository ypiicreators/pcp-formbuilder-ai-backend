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
from typing import Any

_SECTION_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_SERVICE_NAME_RE = re.compile(r"^service\s*name\s*:\s*(.+)$", re.IGNORECASE)
_HEADER_HINT = re.compile(r"field\s*name|^\s*name\s*$", re.IGNORECASE)
_CROSS_FIELD_MARKER = re.compile(
    r"cross[\s-]*field\s*validators?|validators?\s*\(\s*not\s+a\s+section\s*\)",
    re.IGNORECASE,
)
_CROSS_FIELD_ID_HINT = re.compile(r"validator\s*id", re.IGNORECASE)

_EMPTY_DEP = frozenset({"—", "-", "–", "", "n/a", "na", "none"})

#: Catalog tables (Service Name | Section Name | Field Name) write types in prose.
_CATALOG_TYPE_ALIASES: tuple[tuple[str, str], ...] = (
    ("drop down", "select"),
    ("dropdown", "select"),
    ("text box", "text"),
    ("textbox", "text"),
    ("textarea", "textarea"),
    ("calender", "date"),
    ("calendar", "date"),
    ("checkbox", "checkbox"),
    ("radio", "radio"),
    ("pincode", "pincode"),
    ("pin code", "pincode"),
    ("phone", "phone"),
    ("mobile", "phone"),
    ("email", "email"),
    ("file", "file"),
)


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


def _map_catalog_header(parts: list[str]) -> dict[str, int] | None:
    """Service Name | Section Name | Field Name (no Type column)."""
    lowered = [_norm_header_cell(p) for p in parts]
    if any(c in {"type", "fieldtype"} for c in lowered):
        return None
    mapping: dict[str, int] = {}
    for i, cell in enumerate(lowered):
        if "servicename" in cell or cell == "service":
            mapping["service"] = i
        elif "sectionname" in cell or cell == "section":
            mapping["section"] = i
        elif "fieldname" in cell or cell in {"field", "fields"}:
            mapping["field"] = i
    if "section" in mapping and "field" in mapping:
        return mapping
    return None


def _type_from_catalog_rest(rest: str) -> tuple[str, str]:
    """Return (canonical_type, leftover) from the text after a field's colon."""
    low = (rest or "").strip().lower()
    if not low:
        return "", ""
    for alias, canon in _CATALOG_TYPE_ALIASES:
        if low.startswith(alias):
            extra = rest.strip()[len(alias) :].strip(" -:;")
            return canon, extra
    return "", rest.strip()


def _default_catalog_type(name: str, section_title: str) -> str:
    sec = (section_title or "").lower()
    n = (name or "").lower()
    if "document" in sec or any(
        h in n
        for h in (
            "application form",
            "copy of",
            "proof",
            "letter",
            "site plan",
            "photograph",
            "document",
        )
    ):
        return "file"
    if "email" in n:
        return "email"
    if "mobile" in n or "phone" in n:
        return "phone"
    if "pincode" in n or "pin code" in n:
        return "pincode"
    if "date" in n:
        return "date"
    if n in {
        "gender",
        "marital status",
        "nomenclature",
        "state",
        "district",
        "tehsil",
        "region",
        "village",
        "police station",
    }:
        return "select"
    if "address" in n:
        return "textarea"
    return "text"


def _one_catalog_field(raw: str, section_title: str) -> SpecField | None:
    line = re.sub(r"^\d+\.\s*", "", (raw or "").strip())
    if not line:
        return None
    depends = ""
    dep_m = re.search(r"\((depends on [^)]+)\)", line, re.IGNORECASE)
    if dep_m:
        depends = dep_m.group(1).strip()
        line = line[: dep_m.start()] + line[dep_m.end() :]
    mandatory = ""
    man_m = re.search(r"\(([^)]*mandatory[^)]*|optional)\)", line, re.IGNORECASE)
    if man_m:
        mandatory = man_m.group(1).strip()
        line = line[: man_m.start()] + line[man_m.end() :]
    line = re.sub(r"\s+", " ", line).strip(" :-")
    ftype = ""
    if ":" in line:
        name, rest = line.split(":", 1)
        name = name.strip()
        ftype, extra = _type_from_catalog_rest(rest)
        if extra:
            depends = f"{depends} {extra}".strip() if depends else extra
    else:
        name = line
        if re.search(r"\bcheckbox\b", name, re.IGNORECASE):
            ftype = "checkbox"
            name = re.sub(r"\bcheckbox\b", "", name, flags=re.IGNORECASE).strip()
    if not name:
        return None
    hinted = _default_catalog_type(name, section_title)
    if not ftype:
        ftype = hinted
    elif hinted in {"email", "phone", "pincode", "date"} and ftype == "text":
        ftype = hinted
    return SpecField(
        name=name,
        field_type=ftype,
        mandatory=mandatory,
        dependency=depends,
        raw_line=raw,
    )


def _catalog_fields_from_cell(raw: str, section_title: str) -> list[SpecField]:
    text = (raw or "").strip()
    if not text:
        return []
    chunks: list[str]
    if re.search(r"\d+\.\s+\S", text) and len(re.findall(r"\d+\.\s+", text)) >= 2:
        chunks = [c.strip() for c in re.split(r"(?=\d+\.\s)", text) if c.strip()]
    else:
        chunks = [text]
    out: list[SpecField] = []
    for chunk in chunks:
        field = _one_catalog_field(chunk, section_title)
        if field is not None:
            out.append(field)
    return out


def _parse_catalog_spec(text: str) -> ParsedFieldSpec | None:
    column_map: dict[str, int] | None = None
    order: list[str] = []
    by_title: dict[str, list[SpecField]] = {}
    service_name: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        header = _map_catalog_header(parts)
        if header is not None:
            column_map = header
            continue
        if column_map is None:
            continue
        section = _cell(parts, column_map["section"])
        field_raw = _cell(parts, column_map["field"])
        if "service" in column_map:
            svc = _cell(parts, column_map["service"])
            if svc and service_name is None:
                service_name = svc
        if not section or not field_raw:
            continue
        parsed_fields = _catalog_fields_from_cell(field_raw, section)
        if not parsed_fields:
            continue
        if section not in by_title:
            order.append(section)
            by_title[section] = []
        by_title[section].extend(parsed_fields)

    if len(order) < 2:
        return None
    total = sum(len(by_title[t]) for t in order)
    if total < 3:
        return None
    sections = [
        SpecSection(number=i, title=title, fields=by_title[title])
        for i, title in enumerate(order, start=1)
    ]
    return ParsedFieldSpec(sections=sections, service_name=service_name)


def parse_field_spec_document(text: str) -> ParsedFieldSpec | None:
    """
    Return sections (and optional service name) when text matches a known table pattern.

    Understands:
      - numbered sections + Field Name | Type | Mandatory rows
      - catalog tables: Service Name | Section Name | Field Name

    Returns None when the pattern is not detected (caller falls back to one-shot LLM).
    """
    if not (text or "").strip():
        return None

    catalog = _parse_catalog_spec(text)
    if catalog is not None:
        return catalog


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
            title = sec_match.group(2).strip()
            # "1. Nomenclature (Mandatory) : Dropdown" is a field, not a section.
            if current is not None and _looks_like_numbered_field_title(title):
                outlined = _one_catalog_field(line, current.title)
                if outlined is not None:
                    current.fields.append(outlined)
                continue
            if cross_field_mode:
                cross_field_mode = False
                cross_column_map = None
            if current is not None:
                sections.append(current)
            current = SpecSection(
                number=int(sec_match.group(1)),
                title=title,
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
            if _looks_like_outline_field(line):
                outlined = _one_catalog_field(line, current.title)
                if outlined is not None:
                    current.fields.append(outlined)
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
        titled = _parse_titled_outline(text)
        if titled is not None:
            return titled
        return None

    total_fields = sum(len(s.fields) for s in sections)
    if total_fields < 3:
        titled = _parse_titled_outline(text)
        if titled is not None:
            return titled
        return None

    return ParsedFieldSpec(
        sections=sections,
        service_name=service_name,
        cross_field_validators=cross_field_validators,
    )


def _looks_like_numbered_field_title(title: str) -> bool:
    low = (title or "").lower()
    if ":" in title:
        return True
    return any(
        tok in low
        for tok in (
            "mandatory",
            "optional",
            "dropdown",
            "textbox",
            "checkbox",
            "depends on",
        )
    )


def _looks_like_outline_field(line: str) -> bool:
    """A list item or short label under a section heading, not a prose sentence."""
    text = (line or "").strip()
    if len(text) < 2 or len(text) > 220:
        return False
    if _SERVICE_NAME_RE.match(text) or _CROSS_FIELD_MARKER.search(text):
        return False
    if re.match(r"^\d+\.\s+\S", text):
        return True
    low = text.lower()
    if any(
        tok in low
        for tok in (
            "mandatory",
            "optional",
            "dropdown",
            "textbox",
            "checkbox",
            "depends on",
        )
    ):
        return True
    if len(text) <= 80 and not text.endswith("."):
        return True
    return False


_TITLE_LAST_WORDS = frozenset(
    {"details", "documents", "document", "information", "declaration", "upload"}
)


def _is_section_title_line(line: str) -> bool:
    if "|" in line or len(line) < 2 or len(line) > 80:
        return False
    if re.match(r"^\d+\.\s+\S.+:", line):
        return False
    words = re.sub(r"[^a-z0-9\s]", " ", line.lower()).split()
    if not words:
        return False
    if words[0] == "section":
        return True
    return words[-1] in _TITLE_LAST_WORDS


def _parse_titled_outline(text: str) -> ParsedFieldSpec | None:
    """Unnumbered headings (Application Details) followed by a field list."""
    service_name: str | None = None
    sections: list[SpecSection] = []
    current: SpecSection | None = None
    n = 0
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        svc = _SERVICE_NAME_RE.match(line)
        if svc:
            service_name = svc.group(1).strip()
            continue
        if _is_section_title_line(line):
            if current is not None:
                sections.append(current)
            n += 1
            current = SpecSection(number=n, title=line)
            continue
        if current is None or "|" in line:
            continue
        if _looks_like_outline_field(line):
            field = _one_catalog_field(line, current.title)
            if field is not None:
                current.fields.append(field)
    if current is not None:
        sections.append(current)
    sections = [s for s in sections if s.fields]
    if len(sections) < 2:
        return None
    if sum(len(s.fields) for s in sections) < 3:
        return None
    return ParsedFieldSpec(sections=sections, service_name=service_name)


def spec_from_object(obj: Any) -> ParsedFieldSpec | None:
    """
    Coerce model JSON (any layout) into a ParsedFieldSpec.

    Accepts {serviceName, sections:[{title, fields:[{name,type,mandatory,dependency}]}]}
    and light aliases (label, field_type, rules).
    """
    if not isinstance(obj, dict):
        return None
    service = obj.get("serviceName") or obj.get("service_name") or obj.get("service")
    service_name = str(service).strip() if service else None
    raw_sections = obj.get("sections")
    if not isinstance(raw_sections, list):
        return None
    sections: list[SpecSection] = []
    for i, sec in enumerate(raw_sections, start=1):
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or sec.get("name") or "").strip()
        if not title:
            continue
        fields: list[SpecField] = []
        for row in sec.get("fields") or []:
            if isinstance(row, str):
                parsed = _one_catalog_field(row, title)
                if parsed is not None:
                    fields.append(parsed)
                continue
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("label") or "").strip()
            if isinstance(row.get("label"), dict):
                name = str(row["label"].get("en") or name).strip()
            if not name:
                continue
            ftype = str(row.get("type") or row.get("field_type") or row.get("fieldType") or "text")
            fields.append(
                SpecField(
                    name=name,
                    field_type=ftype,
                    mandatory=str(row.get("mandatory") or row.get("required") or ""),
                    dependency=str(
                        row.get("dependency") or row.get("rules") or row.get("logic") or ""
                    ),
                    raw_line=name,
                )
            )
        if fields:
            sections.append(SpecSection(number=i, title=title, fields=fields))
    if not sections or sum(len(s.fields) for s in sections) < 1:
        return None
    return ParsedFieldSpec(sections=sections, service_name=service_name or None)


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