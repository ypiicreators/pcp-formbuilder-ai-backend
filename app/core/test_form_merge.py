"""Unit tests for appending document-generated sections onto an existing form."""

from app.core.form_merge import merge_generated_sections


def _section(sid: str, *fields: tuple[str, str]) -> dict:
    return {
        "id": sid,
        "title": {"en": sid.replace("_", " ").title(), "pa": ""},
        "fields": [
            {"id": fid, "type": "text", "label": {"en": label, "pa": ""}}
            for fid, label in fields
        ],
    }


def test_append_preserves_existing_section_fields():
    base = {
        "sections": [
            _section("applicant_details", ("full_name", "Full Name"), ("mobile", "Mobile")),
        ],
        "crossFieldValidators": [],
    }
    generated = {
        "sections": [
            _section("property_details", ("plot_no", "Plot No"), ("area", "Area")),
        ],
    }

    merged, warnings = merge_generated_sections(base, generated)

    assert len(merged["sections"]) == 2
    assert merged["sections"][0]["id"] == "applicant_details"
    assert [f["id"] for f in merged["sections"][0]["fields"]] == [
        "full_name",
        "mobile",
    ]
    assert merged["sections"][1]["id"] == "property_details"
    assert [f["id"] for f in merged["sections"][1]["fields"]] == ["plot_no", "area"]
    assert any("Appended 1 section" in w for w in warnings)


def test_colliding_field_ids_are_renamed():
    base = {
        "sections": [_section("section_one", ("name", "Name"), ("address", "Address"))],
    }
    generated = {
        "sections": [
            {
                "id": "section_two",
                "title": {"en": "Section Two", "pa": ""},
                "fields": [
                    {"id": "name", "type": "text", "label": {"en": "Name", "pa": ""}},
                    {
                        "id": "city",
                        "type": "text",
                        "label": {"en": "City", "pa": ""},
                        "visibleWhen": {"name": ["x"]},
                    },
                ],
            }
        ],
    }

    merged, warnings = merge_generated_sections(base, generated)

    # Existing section untouched.
    assert [f["id"] for f in merged["sections"][0]["fields"]] == ["name", "address"]

    new_fields = merged["sections"][1]["fields"]
    assert new_fields[0]["id"] == "name_2"
    assert new_fields[1]["id"] == "city"
    # Reference inside the NEW section rewritten to the renamed id.
    assert new_fields[1]["visibleWhen"] == {"name_2": ["x"]}
    assert any("Renamed" in w for w in warnings)


def test_colliding_section_ids_are_renamed():
    base = {"sections": [_section("details", ("a", "A"))]}
    generated = {"sections": [_section("details", ("b", "B"))]}

    merged, _ = merge_generated_sections(base, generated)

    assert merged["sections"][0]["id"] == "details"
    assert merged["sections"][1]["id"] == "details_2"
