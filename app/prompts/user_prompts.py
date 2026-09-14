"""
Mode-specific user prompts + the repair prompt for the PCP AI Form Builder.

The system prompt (system_prompt.py) carries the schema rules. These user prompts
carry the admin's input for each mode:

  - generate_nl   : natural-language description -> whole form
  - generate_doc  : uploaded document + instruction -> whole form
  - edit_json     : existing form + instruction -> ID-anchored change-set
  - repair        : previous invalid output + validation errors -> corrected output
"""

import json

# Default instruction inserted server-side when the admin leaves the document-mode
# instruction blank (per API contract section 4.7).
DEFAULT_DOC_INSTRUCTION: str = (
    "Infer the citizen application form required to produce/obtain this document."
)


def build_nl_prompt(description: str) -> str:
    """Mode A -- natural language. The description IS the instruction (required)."""
    return f"""\
MODE: generate_from_description

Create a Form Builder configuration for the following requirement. Infer sensible
field types, validations, and options. Use "en" labels (add "pa" only if the
requirement provides Punjabi text).

REQUIREMENT:
\"\"\"
{description.strip()}
\"\"\"
"""


def build_doc_prompt(instruction: str | None) -> str:
    """
    Mode B -- document upload, native document input.

    The document itself is attached to the provider call as an attachment; this
    prompt only carries the intent. If the instruction is blank, the server
    default is used.
    """
    admin_instruction = (instruction or "").strip() or DEFAULT_DOC_INSTRUCTION
    return f"""\
MODE: generate_from_document

A document is ATTACHED to this request (you can read its text, layout, and any
images/tables directly). The document may be a CERTIFICATE or an OUTPUT template,
not the application form itself. Your task is to infer the APPLICATION FORM a
citizen would fill in to obtain/produce this document -- NOT to transcribe the
document.

Rules specific to this mode:
- Follow the ADMIN INSTRUCTION below as the primary intent. The document is the
  source content; the instruction tells you what to do with it.
- Turn each piece of information the document requires into an input field.
- Choose specialised field types where appropriate (dates -> date, phone -> phone,
  Aadhaar -> aadhaar, PIN code -> pincode, supporting scans -> file).
- Group related fields into sections (and subSections where it clarifies structure).
- Do NOT copy issuing-authority text, signatures, or seals into fields.

ADMIN INSTRUCTION:
\"\"\"
{admin_instruction}
\"\"\"
"""


def build_doc_prompt_text_fallback(instruction: str | None, extracted_text: str) -> str:
    """
    Mode B fallback -- used when the active provider cannot accept attachments.

    Same intent and rules as build_doc_prompt, but the document content is injected
    as extracted text instead of an attachment.
    """
    admin_instruction = (instruction or "").strip() or DEFAULT_DOC_INSTRUCTION
    return f"""\
MODE: generate_from_document

The text below was EXTRACTED from a document a citizen or department provided. The
document may be a CERTIFICATE or an OUTPUT template, not the application form
itself. Your task is to infer the APPLICATION FORM a citizen would fill in to
obtain/produce this document -- NOT to transcribe the document.

Rules specific to this mode:
- Follow the ADMIN INSTRUCTION below as the primary intent. The document text is
  the source content; the instruction tells you what to do with it.
- Turn each piece of information the document requires into an input field.
- Choose specialised field types where appropriate (dates -> date, phone -> phone,
  Aadhaar -> aadhaar, PIN code -> pincode, supporting scans -> file).
- Group related fields into sections (and subSections where it clarifies structure).
- Do NOT copy issuing-authority text, signatures, or seals into fields.

ADMIN INSTRUCTION:
\"\"\"
{admin_instruction}
\"\"\"

EXTRACTED DOCUMENT TEXT:
\"\"\"
{extracted_text.strip()}
\"\"\"
"""


def build_edit_prompt(context: dict, instruction: str) -> str:
    """
    Mode C -- edit existing form. Produces an ID-anchored CHANGE-SET (not the
    whole form).

    `context` is the COMPACT, context-selected slice of the form (from
    context_selector) -- a small skeleton of ids/types/structure, NOT the full
    production JSON. The full form stays server-side; the change-set is applied
    to it there. Reference fields by the ids shown in the context.
    """
    context_text = json.dumps(context, indent=2, ensure_ascii=False)
    return f"""\
MODE: edit_existing

You are given a COMPACT VIEW of an existing Form Builder form (ids, types, and
structure only -- not the full form) and an instruction. Produce a CHANGE-SET: a
JSON array of ID-ANCHORED operations describing ONLY the deltas the instruction
requires. Reference every target by its ID, never by position. Do not restate the
form. Do not touch anything the instruction did not mention.

SUPPORTED OPERATIONS (use only these):
[
  {{ "op": "setProp",        "fieldId": "<id>", "property": "<name>", "value": <any> }},
  {{ "op": "unsetProp",      "fieldId": "<id>", "property": "<name>" }},
  {{ "op": "setColumnProp",  "tableId": "<table_id>", "fieldId": "<column_id>",
     "property": "<name>", "value": <any> }},
  {{ "op": "unsetColumnProp","tableId": "<table_id>", "fieldId": "<column_id>",
     "property": "<name>" }},
  {{ "op": "addOption",      "fieldId": "<select_id>",
     "value": {{ "value": "<opt_value>", "label": {{ "en": "<text>" }} }} }},
  {{ "op": "removeOption",   "fieldId": "<select_id>", "optionValue": "<opt_value>" }},
  {{ "op": "addField",       "sectionId": "<section_id>",
     "value": {{ "id": "<new_id>", "type": "<FieldType>", "label": {{ "en": "<text>" }} }} }},
  {{ "op": "removeField",    "fieldId": "<id>" }}
]

RULES:
- Every fieldId / tableId / sectionId MUST exist in the COMPACT VIEW below (a new
  field created by addField is the only exception).
- Never change a field's "id" or its "order" (these are protected).
- Never edit top-level metadata (serviceCode, title, description, category, version).
- setProp replaces ONE property -- never re-emit a whole field (that drops locales).
- If a field shows "features" (e.g. visibleWhen, conditionalAutoFillWhen), respect
  those relationships when changing it.
- Output the JSON array ONLY. No markdown fences, no commentary.

COMPACT VIEW OF THE FORM:
\"\"\"
{context_text}
\"\"\"

INSTRUCTION:
\"\"\"
{instruction.strip()}
\"\"\"
"""


def build_repair_prompt(previous_output: str, errors: list[str], is_edit_mode: bool) -> str:
    """
    Repair prompt for the validate -> repair loop. Given the previous (invalid)
    output and the exact validation errors, instruct the model to fix ONLY those.
    """
    error_block = "\n".join(f"- {e}" for e in errors)
    output_format = (
        "an ID-anchored change-set (JSON array)"
        if is_edit_mode
        else "the full form object (flat, top-level keys)"
    )
    return f"""\
MODE: repair

Your previous output failed validation. Fix ONLY the listed problems and return
the corrected result in the same format you produced before: {output_format}. Do
not introduce new fields, do not re-emit untouched fields wholesale, and do not
change anything unrelated to these errors. Output JSON only, no markdown fences.

VALIDATION ERRORS:
\"\"\"
{error_block}
\"\"\"

YOUR PREVIOUS OUTPUT:
\"\"\"
{previous_output.strip()}
\"\"\"
"""
