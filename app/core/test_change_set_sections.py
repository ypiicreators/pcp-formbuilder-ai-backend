"""Section id resolution for edit change-sets."""

from app.core.change_set import Op, OpType, apply_change_set, parse_change_set


def test_addfield_resolves_invented_section_to_only_section():
    form = {
        "sections": [
            {
                "id": "district_details",
                "title": {"en": "District Details"},
                "fields": [{"id": "district", "type": "select", "label": {"en": "District"}}],
            }
        ]
    }
    raw = [
        {
            "op": "addField",
            "sectionId": "address",
            "value": {"id": "state", "type": "select", "label": {"en": "State"}},
        }
    ]
    parsed = parse_change_set(raw)
    assert parsed.ok
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    ids = [f["id"] for f in applied.form["sections"][0]["fields"]]
    assert "state" in ids


def test_add_section_then_add_field():
    form = {"sections": []}
    raw = [
        {
            "op": "addSection",
            "sectionId": "address",
            "value": {"id": "address", "title": {"en": "Address"}},
        },
        {
            "op": "addField",
            "sectionId": "address",
            "value": {"id": "state", "type": "select", "label": {"en": "State"}},
        },
    ]
    parsed = parse_change_set(raw)
    assert parsed.ok
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    assert len(applied.form["sections"]) == 1
    assert applied.form["sections"][0]["fields"][0]["id"] == "state"