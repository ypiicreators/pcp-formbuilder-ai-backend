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