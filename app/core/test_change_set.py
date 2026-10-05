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
