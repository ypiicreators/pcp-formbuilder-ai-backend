"""Unit tests for section-level change-set ops and hallucinated-op errors."""

from app.core.change_set import (
    apply_change_set,
    build_diff,
    list_changed_ids,
    parse_change_set,
)


def _form() -> dict:
    return {
        "sections": [
            {
                "id": "property_search",
                "title": {"en": "Search Property"},
                "fields": [
                    {
                        "id": "search_method",
                        "type": "radio",
                        "label": {"en": "Search Property By"},
                        "options": [
                            {"value": "PROPERTY_ID", "label": {"en": "Id"}},
                            {"value": "SCHEME_NAME", "label": {"en": "Scheme"}},
                        ],
                    }
                ],
            },
            {
                "id": "property_detail",
                "title": {"en": "Property Detail"},
                "fields": [
                    {
                        "id": "scheme_name",
                        "type": "text",
                        "label": {"en": "Scheme Name"},
                    }
                ],
            },
        ]
    }


def test_set_section_prop_visible_when():
    parsed = parse_change_set([
        {
            "op": "setSectionProp",
            "sectionId": "property_detail",
            "property": "visibleWhen",
            "value": {"search_method": ["PROPERTY_ID", "SCHEME_NAME"]},
        }
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(_form(), parsed.ops)
    assert applied.ok, applied.errors
    section = applied.form["sections"][1]
    assert section["visibleWhen"] == {
        "search_method": ["PROPERTY_ID", "SCHEME_NAME"]
    }
    assert section["fields"][0]["id"] == "scheme_name"


def test_unset_section_prop():
    form = _form()
    form["sections"][1]["visibleWhen"] = {"search_method": ["PROPERTY_ID"]}
    parsed = parse_change_set([
        {
            "op": "unsetSectionProp",
            "sectionId": "property_detail",
            "property": "visibleWhen",
        }
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    assert "visibleWhen" not in applied.form["sections"][1]


def test_unknown_update_column_has_hint():
    parsed = parse_change_set([
        {"op": "addColumn", "value": {"id": "x", "type": "text"}},
        {"op": "updateColumn", "fieldId": "scheme_name"},
    ])
    assert not parsed.ok
    assert any("addColumn requires 'tableId'" in e for e in parsed.errors)
    assert any("unknown op 'updateColumn'" in e and "setColumnProp" in e
               for e in parsed.errors)


def test_section_visible_when_shows_in_diff():
    original = _form()
    parsed = parse_change_set([
        {
            "op": "setSectionProp",
            "sectionId": "property_detail",
            "property": "visibleWhen",
            "value": {"search_method": ["PROPERTY_ID", "SCHEME_NAME"]},
        }
    ])
    applied = apply_change_set(original, parsed.ops)
    changed = list_changed_ids(original, applied.form)
    assert "property_detail" in changed
    diffs = build_diff(original, applied.form, changed)
    by_id = {d.field_id: d for d in diffs}
    assert by_id["property_detail"].after["visibleWhen"] == {
        "search_method": ["PROPERTY_ID", "SCHEME_NAME"]
    }
    assert "fields" not in (by_id["property_detail"].after or {})


def test_move_field_after_anchor():
    form = {
        "sections": [
            {
                "id": "service_details",
                "title": {"en": "Service Details"},
                "fields": [
                    {"id": "region", "type": "select", "label": {"en": "Region"}},
                    {"id": "business_address", "type": "text", "label": {"en": "Business Address"}},
                    {"id": "village", "type": "select", "label": {"en": "Village"}},
                ],
            }
        ]
    }
    parsed = parse_change_set([
        {"op": "moveField", "fieldId": "village", "afterFieldId": "region"},
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    ids = [f["id"] for f in applied.form["sections"][0]["fields"]]
    assert ids == ["region", "village", "business_address"]
    changed = list_changed_ids(form, applied.form)
    assert "village" in changed


def test_move_field_into_new_section():
    form = {
        "sections": [
            {
                "id": "documents",
                "title": {"en": "Documents"},
                "fields": [
                    {"id": "application_form", "type": "file", "label": {"en": "Application Form"}},
                    {"id": "declaration", "type": "checkbox", "label": {"en": "Declaration"}},
                ],
            }
        ]
    }
    parsed = parse_change_set([
        {
            "op": "addSection",
            "sectionId": "declaration",
            "value": {"id": "declaration", "title": {"en": "Declaration"}},
        },
        {"op": "moveField", "fieldId": "declaration", "sectionId": "declaration"},
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    docs = [f["id"] for f in applied.form["sections"][0]["fields"]]
    decl = [f["id"] for f in applied.form["sections"][1]["fields"]]
    assert docs == ["application_form"]
    assert decl == ["declaration"]
    assert applied.form["sections"][1]["id"] == "declaration"


def test_add_section_with_copied_field_moves_instead_of_duplicating():
    form = {
        "sections": [
            {
                "id": "documents",
                "fields": [
                    {"id": "declaration", "type": "checkbox", "label": {"en": "Declaration"}},
                ],
            }
        ]
    }
    parsed = parse_change_set([
        {
            "op": "addSection",
            "sectionId": "declaration",
            "value": {
                "id": "declaration",
                "title": {"en": "Declaration"},
                "fields": [
                    {"id": "declaration", "type": "checkbox", "label": {"en": "Declaration"}},
                ],
            },
        }
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    ids = [
        f["id"]
        for sec in applied.form["sections"]
        for f in sec.get("fields") or []
    ]
    assert ids == ["declaration"]
    assert applied.form["sections"][0]["fields"] == []
    assert applied.form["sections"][1]["fields"][0]["id"] == "declaration"


def test_add_field_existing_id_moves():
    form = {
        "sections": [
            {
                "id": "documents",
                "fields": [
                    {"id": "declaration", "type": "checkbox", "label": {"en": "I agree"}},
                ],
            },
            {"id": "declaration", "title": {"en": "Declaration"}, "fields": []},
        ]
    }
    parsed = parse_change_set([
        {
            "op": "addField",
            "sectionId": "declaration",
            "value": {"id": "declaration", "type": "checkbox", "label": {"en": "I agree"}},
        }
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    assert [f["id"] for f in applied.form["sections"][0]["fields"]] == []
    assert applied.form["sections"][1]["fields"][0]["label"]["en"] == "I agree"


def test_move_field_requires_anchor():
    parsed = parse_change_set([
        {"op": "moveField", "fieldId": "village"},
    ])
    assert not parsed.ok
    assert any("afterFieldId" in e for e in parsed.errors)


def test_set_column_prop_missing_property_hints_move_field():
    parsed = parse_change_set([
        {"op": "setColumnProp", "tableId": "t", "fieldId": "village"},
        {"op": "setColumnProp", "tableId": "t", "fieldId": "region"},
    ])
    assert not parsed.ok
    assert any("moveField" in e for e in parsed.errors)


def test_cannot_replace_section_fields_array():
    parsed = parse_change_set([
        {
            "op": "setSectionProp",
            "sectionId": "property_detail",
            "property": "fields",
            "value": [],
        }
    ])
    assert not parsed.ok
    assert any("protected section property 'fields'" in e for e in parsed.errors)


def test_set_prop_autofill_when_static_default():
    """Enable Default Value → autoFillWhen form_load + static source."""
    parsed = parse_change_set([
        {
            "op": "setProp",
            "fieldId": "scheme_name",
            "property": "autoFillWhen",
            "value": {
                "field": "form_load",
                "value": "true",
                "source": {"type": "static", "value": "test schme 8"},
            },
        }
    ])
    assert parsed.ok, parsed.errors
    applied = apply_change_set(_form(), parsed.ops)
    assert applied.ok, applied.errors
    field = applied.form["sections"][1]["fields"][0]
    assert field["autoFillWhen"] == {
        "field": "form_load",
        "value": "true",
        "source": {"type": "static", "value": "test schme 8"},
    }
