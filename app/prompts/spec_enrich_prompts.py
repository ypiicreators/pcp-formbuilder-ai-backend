"""
Compact prompts for spec skeleton -> dependency/autofill enrichment (low token budget).
"""

from __future__ import annotations

import json

from app.prompts.constants import ALLOWED_FIELD_TYPES
from app.prompts.user_prompts import DEFAULT_DOC_INSTRUCTION


def build_spec_enrich_system_prompt() -> str:
    types = ", ".join(ALLOWED_FIELD_TYPES[:20]) + ", ... (use exact FieldType from skeleton)"
    return f"""\
You enrich an existing Form Builder JSON skeleton. Output JSON only, no markdown fences.

RULES:
- Keep EVERY section and EVERY field from the skeleton (same ids, same count, same order).
- Do NOT remove, merge, or rename sections or fields.
- ADD only: visibleWhen, hiddenWhen, dependsOn, dependsOnAny, datasource, autofill/source
  props (source, autoFillFormAadharEkyc, lockAfterAutoFill, options, file allowedTypes/maxSizeMB, etc.
- Do NOT add field-level validators to implement cross-field rules — those live on the form root.
- KEEP root crossFieldValidators from the skeleton unchanged (same ids, expressions, count).
- Do NOT model cross-field validator rows as sections or fields.
- KEEP transliteration / transliterationField already set on skeleton fields; do not remove.
- KEEP required flags from the skeleton unless dependency text implies a change.
- For select/radio/multiselect/applying_for: KEEP a non-empty options array; expand
  placeholder options with sensible values from the dependency text or field label.
- visibleWhen values MUST be arrays of option values; keys MUST be existing field ids.
- Map dependency notes to the correct field ids in the skeleton.
- Allowed field types include: {types}
- Use "en" labels; add "pa" only when the label already implies Punjabi.
"""


def build_spec_enrich_user_prompt(
    skeleton: dict,
    dependency_manifest: list[dict[str, str]],
    instruction: str | None,
) -> str:
    admin = (instruction or "").strip() or DEFAULT_DOC_INSTRUCTION
    skel_json = json.dumps(skeleton, ensure_ascii=False, indent=None)
    manifest_json = json.dumps(dependency_manifest, ensure_ascii=False, indent=None)
    return f"""\
MODE: enrich_form_from_spec_skeleton

Return the COMPLETE form object with all sections/fields from the skeleton, enriched.

ADMIN INSTRUCTION:
\"\"\"
{admin}
\"\"\"

SKELETON (preserve structure exactly):
\"\"\"
{skel_json}
\"\"\"

DEPENDENCY MANIFEST (apply to the fieldIds listed):
\"\"\"
{manifest_json}
\"\"\"
"""


def build_spec_section_repair_system_prompt() -> str:
    return build_spec_enrich_system_prompt()


def build_spec_section_repair_user_prompt(
    section: dict,
    errors: list[str],
    dependency_rows: list[dict[str, str]],
) -> str:
    err_block = "\n".join(f"- {e}" for e in errors)
    return f"""\
MODE: repair_single_section

Fix validation errors for this ONE section only. Output:
{{ "sections": [ <the corrected section object> ] }}

Do not drop fields. Section must keep the same field ids and count unless an error
requires fixing an invalid type.

DEPENDENCY HINTS:
{json.dumps(dependency_rows, ensure_ascii=False)}

ERRORS:
\"\"\"
{err_block}
\"\"\"

SECTION:
\"\"\"
{json.dumps(section, ensure_ascii=False)}
\"\"\"
"""