"""Prompts for layout-agnostic spec extraction (any document shape -> sections/fields)."""

from __future__ import annotations

SPEC_EXTRACT_SYSTEM = """\
You extract a FIELD SPEC from a form-configuration document. You do NOT generate
a Form Builder form. You only list the sections and fields the document defines.

Output a single JSON object, no markdown fences:
{
  "serviceName": string or null,
  "sections": [
    {
      "title": "exact section title from the document",
      "fields": [
        {
          "name": "exact field name",
          "type": "text|select|radio|file|date|phone|email|textarea|checkbox|pincode",
          "mandatory": "Mandatory" or "",
          "dependency": "visibility/depends notes, else empty"
        }
      ]
    }
  ]
}

Rules:
- Use ONLY sections and fields written in the document (tables, lists, headings).
- Keep the document's section count and titles. Do not invent Applicant Details,
  Address Details, Documents, or Declaration unless those titles appear.
- Keep field names verbatim (do not rename or drop Service Details fields).
- If type is unstated, infer from nearby words: Dropdown=select, Textbox=text,
  Calendar=date, Checkbox=checkbox; document lists are type file.
- Ignore narrative that is not a field.
"""


def build_spec_extract_user_prompt(
    instruction: str | None,
    *,
    extracted_text: str | None = None,
) -> str:
    admin = (instruction or "").strip()
    admin_block = f'ADMIN NOTE:\n"""\n{admin}\n"""\n\n' if admin else ""
    if extracted_text and extracted_text.strip():
        return (
            f"{admin_block}"
            "The text below was extracted from the uploaded document. "
            "List every section and field it defines.\n\n"
            'DOCUMENT:\n"""\n'
            f"{extracted_text.strip()}\n"
            '"""\n'
        )
    return (
        f"{admin_block}"
        "The source document is ATTACHED. Read its tables, lists, and headings "
        "and list every section and field it defines.\n"
    )
