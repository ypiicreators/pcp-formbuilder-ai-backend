"""
Mode-specific user prompts for the Workflow Builder AI.

Mirrors user_prompts.py in structure but scoped entirely to the workflow
context. The system prompt (workflow_system_prompt.py) carries the schema rules;
these user prompts carry the admin's input for each mode:

  - generate   : natural-language description -> whole workflow form
  - generate_doc: uploaded document + instruction -> whole workflow form
  - edit        : existing form + instruction -> ID-anchored change-set
  - repair      : previous invalid output + validation errors -> corrected output

Key difference from citizen form prompts: the context framing always says
"officer action UI" not "citizen application form".
"""

from __future__ import annotations

import json

# Default instruction when the admin uploads a document without any instruction.
DEFAULT_WORKFLOW_DOC_INSTRUCTION: str = (
    "Infer the officer action form required to process this document or case."
)


def build_workflow_nl_prompt(description: str) -> str:
    """
    Generate mode — natural language description → whole workflow form.
    The description IS the instruction (required).
    """
    # Detect if the user explicitly asked for sections
    desc_lower = description.lower()
    wants_sections = any(word in desc_lower for word in ["section", "sections", "group", "groups", "grouped"])

    if wants_sections:
        output_instruction = (
            "Since the requirement mentions sections/groups, output a sections object:\n"
            "{ \"sections\": [ { \"id\": \"...\", \"title\": { \"en\": \"...\" }, \"fields\": [...] } ] }"
        )
    else:
        output_instruction = (
            "Output a FLAT JSON ARRAY of fields — NOT wrapped in a sections object.\n"
            "Correct:   [ { \"id\": \"remarks\", \"type\": \"textarea\", ... }, ... ]\n"
            "WRONG:     { \"sections\": [ { \"fields\": [...] } ] }  ← do NOT do this\n"
            "Only use sections if the requirement explicitly asks for sections or grouping."
        )

    return f"""\
MODE: generate_workflow_from_description

Create a Workflow Builder (officer action) form configuration for the following
requirement. This form will be filled by a GOVERNMENT OFFICER when processing an
application — it is NOT a citizen-facing form.

OUTPUT FORMAT RULE (CRITICAL):
{output_instruction}

Infer sensible field types, validations, and structure. Use "en" labels (add "pa"
only if the requirement provides Punjabi text). For officer action forms, always
consider:
  - Remarks fields in English and Punjabi with transliteration
  - File upload fields if documents need to be submitted
  - api_trigger fields if data needs to be fetched or fees calculated
  - activity_timeline to display application history (at most once)

REQUIREMENT:
\"\"\"
{description.strip()}
\"\"\"
"""


def build_workflow_doc_prompt(instruction: str | None) -> str:
    """
    Document mode — native document attachment.

    The document is attached to the provider call directly; this prompt only
    carries the intent. If the instruction is blank, the server default is used.
    """
    admin_instruction = (instruction or "").strip() or DEFAULT_WORKFLOW_DOC_INSTRUCTION
    return f"""\
MODE: generate_workflow_from_document

A document is ATTACHED to this request. Your task is to infer the OFFICER ACTION
FORM that an officer would fill when processing this document or case type — NOT
to transcribe the document itself.

Rules specific to this mode:
- Follow the ADMIN INSTRUCTION below as the primary intent.
- Turn each piece of information the officer needs to enter into a field.
- Choose appropriate officer-context field types (remarks → textarea with
  transliteration, files → file/upload, calculations → api_trigger or calculated).
- Group related fields into sections where it improves clarity.
- Do NOT copy issuing-authority text, signatures, or seals into fields.

ADMIN INSTRUCTION:
\"\"\"
{admin_instruction}
\"\"\"
"""


def build_workflow_doc_prompt_text_fallback(
    instruction: str | None, extracted_text: str
) -> str:
    """
    Document mode fallback — used when the provider cannot accept attachments
    (DOCX text extraction path). Same intent as build_workflow_doc_prompt but
    the document content is injected as extracted text.
    """
    admin_instruction = (instruction or "").strip() or DEFAULT_WORKFLOW_DOC_INSTRUCTION
    return f"""\
MODE: generate_workflow_from_document

The text below was EXTRACTED from a document. Your task is to infer the OFFICER
ACTION FORM that an officer would fill when processing this document or case type
— NOT to transcribe the document.

Rules specific to this mode:
- Follow the ADMIN INSTRUCTION below as the primary intent.
- Turn each piece of information the officer needs to enter into a field.
- Choose appropriate officer-context field types (remarks → textarea with
  transliteration, files → file/upload, calculations → api_trigger or calculated).
- Group related fields into sections where it improves clarity.
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


def build_workflow_edit_prompt(
    context: dict,
    instruction: str,
    target_field_ids: list[str] | None = None,
) -> str:
    """
    Edit mode — produce an ID-anchored CHANGE-SET (not the whole form).

    `context` is the compact, context-selected slice of the workflow form
    (from context_selector) — a small skeleton of ids/types/structure, NOT
    the full production JSON. The full form stays server-side; the change-set
    is applied to it there. Reference fields by the ids shown in the context.
    """
    context_text = json.dumps(context, indent=2, ensure_ascii=False)
    target_section = ""
    if target_field_ids:
        targets_list = "\n".join(f"- {tid}" for tid in target_field_ids)
        target_section = f"""
TARGET FIELD(S) SPECIFIED BY USER:
The user explicitly selected and attached the following field(s) for this edit:
{targets_list}

Apply the requested changes specifically to the target field(s) listed above. Do not modify other fields unless strictly necessary due to dependency rules.
"""
    return f"""\
MODE: edit_workflow

You are given a COMPACT VIEW of an existing Workflow Builder officer-action form
(ids, types, and structure only — not the full form) and an instruction. Produce
a CHANGE-SET: a JSON array of ID-ANCHORED operations describing ONLY the deltas
the instruction requires. Reference every target by its ID, never by position.
Do not restate the form. Do not touch anything the instruction did not mention.

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
  {{ "op": "removeField",    "fieldId": "<id>" }},
  {{ "op": "addColumn",      "tableId": "<table_id>",
     "value": {{ "id": "<column_id>", "type": "<TableColumnType>", "label": {{ "en": "<text>" }} }} }},
  {{ "op": "removeColumn",   "tableId": "<table_id>", "fieldId": "<column_id>" }}
]

WORKFLOW BUILDER RULES FOR THIS CHANGE-SET:
- Every fieldId / tableId / sectionId MUST exist in the COMPACT VIEW below
  (a new field created by addField, or a new column created by addColumn, are
  the only exceptions).
- Never change a field's "id" or "order".
- Never add citizen-only types (applying_for, mobile_verification, eKYC types, etc).
- For transliteration: use "enableTransliteration" (boolean), NOT "transliteration".
- For table_view columns: use "tableViewColumns", NOT "columns".
- To add columns on a type "table" field, use addColumn — never addField.
- Singleton types (activity_timeline, previous_history, role_wise_history) may
  appear AT MOST ONCE — never add a second one.
- Output the JSON array ONLY. No markdown fences, no commentary.
{target_section}
COMPACT VIEW OF THE FORM:
\"\"\"
{context_text}
\"\"\"

INSTRUCTION:
\"\"\"
{instruction.strip()}
\"\"\"
"""


def build_workflow_repair_prompt(
    previous_output: str, errors: list[str], is_edit_mode: bool
) -> str:
    """
    Repair prompt for the validate → repair loop.
    Given the previous (invalid) output and the exact validation errors,
    instruct the model to fix ONLY those errors.
    Mirrors build_repair_prompt() in user_prompts.py.
    """
    error_block = "\n".join(f"- {e}" for e in errors)
    output_format = (
        "an ID-anchored change-set (JSON array)"
        if is_edit_mode
        else "the full workflow form object (sections wrapper)"
    )
    return f"""\
MODE: repair_workflow

Your previous output failed validation against the Workflow Builder schema.
Fix ONLY the listed problems and return the corrected result in the same format
you produced before: {output_format}. Do not introduce new fields, do not
re-emit untouched fields wholesale, and do not change anything unrelated to
these errors. Output JSON only, no markdown fences.

Remember the key workflow-builder rules:
- Use "enableTransliteration" (not "transliteration") for script conversion.
- table_view uses "tableViewColumns" (not "columns").
- NEVER use citizen-only types (applying_for, mobile_verification, eKYC types, etc.).
- Singleton types (activity_timeline, previous_history, role_wise_history): max one each.

VALIDATION ERRORS:
\"\"\"
{error_block}
\"\"\"

YOUR PREVIOUS OUTPUT:
\"\"\"
{previous_output.strip()}
\"\"\"
"""
