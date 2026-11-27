"""Tests for KB binding onto forms."""

from app.kb.binder import bind_kb_to_form


def test_api_endpoint_binds_district_and_resolves_state():
    entries = [
        {
            "key": "geo.district_api",
            "kind": "api_endpoint",
            "title": "District",
            "body": "",
            "appliesTo": {"keywords": ["district"], "fieldTypes": ["select"], "modes": []},
            "payload": {
                "url": "api/um/GeoUnit/GetGeoUnitDList",
                "method": "POST",
                "payload": {"parentId": "{state}"},
            },
        }
    ]
    form = {
        "sections": [
            {
                "id": "sec1",
                "title": {"en": "Address"},
                "fields": [
                    {"id": "state", "type": "select", "label": {"en": "State"}, "options": []},
                    {
                        "id": "district",
                        "type": "select",
                        "label": {"en": "District"},
                        "options": [{"value": 1, "label": {"en": "A"}}],
                    },
                ],
            }
        ]
    }
    bound, warnings = bind_kb_to_form(
        form, entries, "create state and district dropdowns"
    )
    state = bound["sections"][0]["fields"][0]
    district = bound["sections"][0]["fields"][1]
    assert state.get("datasource") is None
    assert district.get("datasource") is not None
    assert "options" not in district
    assert district["dependsOnAny"] == ["state"]
    assert district["datasource"]["payload"]["parentId"] == "state"
    assert warnings


def test_parent_keyword_does_not_bind_state_fields():
    # Seeded district entry lists "state" as a keyword, but "state" is its parent role.
    entries = [
        {
            "key": "geo.districts_by_state",
            "kind": "api_endpoint",
            "title": "Districts for a selected State",
            "body": "",
            "appliesTo": {"keywords": ["district", "state", "geo"], "fieldTypes": ["select"], "modes": []},
            "payload": {
                "url": "api/um/GeoUnit/GetGeoUnitDList",
                "method": "POST",
                "payload": {"parentId": "{state}"},
            },
        }
    ]

    def select(fid, label):
        return {"id": fid, "type": "select", "label": {"en": label}, "options": []}

    form = {
        "sections": [
            {
                "id": "sec1",
                "fields": [
                    select("state", "State"),
                    select("state_of_origin", "State of Origin"),
                    select("district", "District"),
                ],
            }
        ]
    }
    bound, _ = bind_kb_to_form(form, entries, "")
    state, origin, district = bound["sections"][0]["fields"]
    assert state.get("datasource") is None
    assert origin.get("datasource") is None
    assert district["kbKey"] == "geo.districts_by_state"
    assert district["dependsOnAny"] == ["state"]


def test_unresolved_parent_skips_binding():
    # No "state" field on the form, so {state} cannot be resolved; do not bind a literal.
    entries = [
        {
            "key": "geo.district_api",
            "kind": "api_endpoint",
            "title": "District",
            "body": "",
            "appliesTo": {"keywords": ["district"], "fieldTypes": ["select"], "modes": []},
            "payload": {"url": "api/x", "method": "POST", "payload": {"parentId": "{state}"}},
        }
    ]
    form = {
        "sections": [
            {"id": "s", "fields": [{"id": "district", "type": "select", "label": {"en": "District"}}]}
        ]
    }
    bound, warnings = bind_kb_to_form(form, entries, "")
    assert bound["sections"][0]["fields"][0].get("datasource") is None
    assert warnings == []