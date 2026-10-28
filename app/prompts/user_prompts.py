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

#: Used when a document is uploaded against an EXISTING form — only new
#: sections for this document should be generated; the server appends them.
DEFAULT_DOC_APPEND_INSTRUCTION: str = (
    "Infer NEW section(s) and fields from this document only. Do not recreate "
    "or replace any existing sections — they will be preserved and your output "
    "will be appended."
)


def build_nl_prompt(description: str) -> str:
    """Mode A -- natural language. The description IS the instruction (required)."""
    return f"""\
MODE: generate_from_description

Create a Form Builder configuration for the following requirement. Infer sensible
field types, validations, and options. Use "en" labels (add "pa" only if the
requirement provides Punjabi text).

If the requirement asks for a total / sum of a specific table column (e.g.
income), that column MUST be type "number" with "showTotal": true. Do not set
showTotal on any other column unless the requirement also asks to sum it.

Use FEATURE CONTRACT property names for advanced config: file OCR (OCR +
doc_type + allowedTypes), API dropdowns (datasource url/method/payload), eKYC
autofill (source + autoFillFormAadharEkyc), PAN autofill, profile autofill,
computed_table.computation, api_trigger api_config, date validators, highlightRules,
requiredExpression, htmlContent on htmlDescription. Never invent aliases.

REQUIREMENT:
\"\"\"
{description.strip()}
\"\"\"
"""


def build_doc_prompt(
    instruction: str | None, *, append_to_existing: bool = False
) -> str:
    """
    Mode B -- document upload, native document input.

    The document itself is attached to the provider call as an attachment; this
    prompt only carries the intent. If the instruction is blank, the server
    default is used.

    When `append_to_existing` is True, the admin already has a form on the
    canvas: instruct the model to emit ONLY new sections for this document
    (the server will append them without touching existing sections).
    """
    default = (
        DEFAULT_DOC_APPEND_INSTRUCTION
        if append_to_existing
        else DEFAULT_DOC_INSTRUCTION
    )
    admin_instruction = (instruction or "").strip() or default
    append_rules = ""
    if append_to_existing:
        append_rules = """
- An existing form ALREADY exists on the canvas. Emit ONLY the NEW section(s)
  and fields inferred from THIS document. Do not recreate prior sections.
- Prefer fresh section/field ids that will not collide with common names already
  used (the server will rename collisions, but unique ids are better).
- The server will APPEND your sections to the existing form; existing fields must
  remain untouched.
"""
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
{append_rules}
ADMIN INSTRUCTION:
\"\"\"
{admin_instruction}
\"\"\"
"""


def build_doc_prompt_text_fallback(
    instruction: str | None,
    extracted_text: str,
    *,
    append_to_existing: bool = False,
) -> str:
    """
    Mode B fallback -- used when the active provider cannot accept attachments.

    Same intent and rules as build_doc_prompt, but the document content is injected
    as extracted text instead of an attachment.
    """
    default = (
        DEFAULT_DOC_APPEND_INSTRUCTION
        if append_to_existing
        else DEFAULT_DOC_INSTRUCTION
    )
    admin_instruction = (instruction or "").strip() or default
    append_rules = ""
    if append_to_existing:
        append_rules = """
- An existing form ALREADY exists on the canvas. Emit ONLY the NEW section(s)
  and fields inferred from THIS document. Do not recreate prior sections.
- Prefer fresh section/field ids that will not collide with common names already
  used (the server will rename collisions, but unique ids are better).
- The server will APPEND your sections to the existing form; existing fields must
  remain untouched.
"""
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
{append_rules}
ADMIN INSTRUCTION:
\"\"\"
{admin_instruction}
\"\"\"

EXTRACTED DOCUMENT TEXT:
\"\"\"
{extracted_text.strip()}
\"\"\"
"""


def build_edit_prompt(
    context: dict,
    instruction: str,
    target_field_ids: list[str] | None = None,
) -> str:
    """
    Mode C -- edit existing form. Produces an ID-anchored CHANGE-SET (not the
    whole form).

    `context` is the COMPACT, context-selected slice of the form (from
    context_selector) -- a small skeleton of ids/types/structure, NOT the full
    production JSON. The full form stays server-side; the change-set is applied
    to it there. Reference fields by the ids shown in the context.
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
  {{ "op": "setSectionProp", "sectionId": "<section_id>", "property": "<name>", "value": <any> }},
  {{ "op": "unsetSectionProp","sectionId": "<section_id>", "property": "<name>" }},
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
     "value": {{ "id": "<column_id>", "type": "<TableColumnType>", "label": {{ "en": "<text>" }},
                "required"?: boolean }} }},
  {{ "op": "removeColumn",   "tableId": "<table_id>", "fieldId": "<column_id>" }}
]

RULES:
- Every fieldId / tableId / sectionId MUST exist in the COMPACT VIEW below (a new
  field created by addField, or a new column created by addColumn, are the only
  exceptions).
- Never invent operation names. There is no updateColumn / updateField /
  updateSection. To change a property, use setProp (field), setSectionProp
  (section), or setColumnProp (table column).
- Never change a field's "id" or its "order" (these are protected). Never
  replace a section's "fields" / "subSections" arrays with setSectionProp.
- Never edit top-level metadata (serviceCode, title, description, category, version).
- setProp / setSectionProp replace ONE property -- never re-emit a whole field
  or section (that drops locales).
- Show/hide a SECTION when a radio/select has a value: setSectionProp
  "visibleWhen" on that sectionId. Values MUST be arrays of option values from
  the compact view. "Any option selected" means list EVERY option value of the
  watched field. Example:
  {{ "op": "setSectionProp", "sectionId": "property_detail",
     "property": "visibleWhen",
     "value": {{ "search_method": ["PROPERTY_ID", "SCHEME_NAME"] }} }}
- Show/hide a FIELD the same way with setProp "visibleWhen" on the fieldId.
  Do NOT use addColumn / addField for visibility.
- "Enable Default Value" / static default / pre-fill on form load for a FIELD:
  setProp "autoFillWhen" on that fieldId. There is NO "defaultValue" property.
  Shape (matches the Form Builder "Enable Default Value" checkbox):
  {{ "op": "setProp", "fieldId": "scheme_name", "property": "autoFillWhen",
     "value": {{ "field": "form_load", "value": "true",
                 "source": {{ "type": "static", "value": "test schme 8" }} }} }}
  Do this once per field. Do NOT invent a summary table, description block, or
  new fields to "show" the values — put autoFillWhen on the existing fields.
  Optional: also setProp "readonly": true if the user asked to lock the value.
- If a field shows "features" (e.g. visibleWhen, conditionalAutoFillWhen), respect
  those relationships when changing it.
- For table fields: row actions, keys, and validators are setProp on the TABLE
  fieldId (disableAddRow, disableEdit, disableDelete, disableEditWhen,
  disableDeleteWhen, rowKey, rowKeyStrategy, rowValidators, tableValidators,
  enableListColumns, listColumns, minRows, maxRows).
- Column flags use setColumnProp (showAsTag, dynamicId, showTotal,
  transliteration, transliterationField, options, conditionalAutoFillWhen,
  minValue, maxValue, maxLength, datasource, OCR, doc_type, allowedTypes,
  format, requiredExpression, api_config, button_props).
- File OCR: setProp OCR=true AND doc_type (aadhaar|pan|passport|driving_license|voter_id)
  plus allowedTypes / maxSizeMB on the file field. Live capture does not use OCR.
- API lookup button: setProp trigger_mode + api_config (endpoint required) on
  api_trigger, or setColumnProp on an _api_trigger/button column.
- Computed table: setProp computation (operation, source_table, match_key) —
  never formula.
- Options from another table: setColumnProp datasource
  {{ "type": "form_field", "sourceField": "<tableId>", "valueKey": "<col>" }}.
- eKYC fill: setProp source to the eKYC type and autoFillFormAadharEkyc to Name/DOB/...
- Dropdown from API: setProp datasource {{ "url", "method", "payload" }}; do not
  use datasource for a hardcoded list.
- Static data on table columns: setColumnProp "options" on EACH column (or put
  "options" inside the addColumn value). If the user says static data / static
  options on ALL columns, emit options for every select/radio/multiselect
  column they named; if they listed values for a text column, change its type
  to select and set options. Never use datasource for a hardcoded list.
  For a static DEFAULT value (pre-fill), setColumnProp conditionalAutoFillWhen
  with autoFill.source "static".
- To ADD columns (city, state, country, a Punjabi pair, etc.) you MUST use
  addColumn on the table id. NEVER use addField for a table column. NEVER
  replace the whole "columns" array with setProp (that drops existing config).
  addColumn requires "tableId" AND a column "value" object with id+type.
  Do not emit addColumn unless the instruction asks to add a table column.
- "Enable transliteration" on a table: do NOT set transliteration on the table
  field itself. Pick the primary text/English column, addColumn a sibling
  {{ "id": "<source>_pa", "type": "text", "label": {{ "en": "<Source> (Punjabi)" }} }}
  if it does not already exist, then setColumnProp transliteration=true and
  transliterationField="<source>_pa" on the SOURCE column.
- "Show as tag": setColumnProp showAsTag=true on the intended column (status,
  type, etc. — if unspecified, the last select/text status-like column).
- "Show Total Sum" / total sum / grand total of a table column: ONLY when the
  instruction explicitly asks for a sum/total of a named column (e.g. income),
  setColumnProp showTotal=true on that NUMBER column (or include
  "showTotal": true in the addColumn value when creating it). If the column is
  not type number, change it to number first (setColumnProp "type": "number")
  then set showTotal. NEVER enable showTotal on columns the user did not ask
  to sum. NEVER put showTotal on the table field itself.
- Do not return an empty array when the instruction asks to add, enable, or
  configure something.
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

CHANGE-SET REPAIR HINTS (edit mode):
- Allowed ops only: setProp, unsetProp, setSectionProp, unsetSectionProp,
  setColumnProp, unsetColumnProp, addOption, removeOption, addField,
  removeField, addColumn, removeColumn. Never invent names like updateColumn.
- Show/hide a section: setSectionProp visibleWhen on sectionId. Values MUST be
  arrays of option values, e.g. {{ "search_method": ["PROPERTY_ID", "SCHEME_NAME"] }}.
- Enable Default Value / static pre-fill on a field: setProp autoFillWhen with
  {{ "field": "form_load", "value": "true",
     "source": {{ "type": "static", "value": "<the default>" }} }}.
  Never use a top-level "defaultValue" key.
- addColumn requires tableId and a column value with id+type. Do not use
  addColumn unless adding a table column.
- Show Total Sum / column total: setColumnProp showTotal=true only on the
  NUMBER column the user asked to sum. Do not invent showTotal elsewhere.
- updateColumn is invalid; use setColumnProp. updateSection is invalid; use
  setSectionProp.

VALIDATION ERRORS:
\"\"\"
{error_block}
\"\"\"

YOUR PREVIOUS OUTPUT:
\"\"\"
{previous_output.strip()}
\"\"\"
"""
