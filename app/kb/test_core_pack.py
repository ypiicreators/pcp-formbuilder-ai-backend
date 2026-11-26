"""The core KB pack must be valid and its geography cascade must bind correctly."""

from app.kb.binder import bind_kb_to_form
from app.kb.core_pack import APIS, CORE_PACK, POLICIES
from app.kb.validate import validate_create


def _as_live(entries):
    return [
        {
            "key": e.key,
            "kind": e.kind.value,
            "title": e.title,
            "body": e.body,
            "appliesTo": e.appliesTo.model_dump(),
            "payload": e.payload,
        }
        for e in entries
    ]


def test_pack_entries_are_valid_and_unique():
    keys = [e.key for e in CORE_PACK]
    assert len(keys) == len(set(keys))
    for e in CORE_PACK:
        assert validate_create(e) == [], e.key


def test_every_policy_has_keywords_so_it_is_not_always_injected():
    for p in POLICIES:
        assert p.appliesTo.keywords, p.key


def _sel(fid, label):
    return {"id": fid, "type": "select", "label": {"en": label}}


def test_geo_cascade_resolves_parent_ids_and_prefers_same_prefix():
    form = {
        "sections": [
            {
                "id": "s",
                "title": {"en": "Address"},
                "fields": [
                    _sel("state", "State"),
                    _sel("district", "District"),
                    _sel("tehsil", "Tehsil"),
                    _sel("village", "Village"),
                    _sel("muncipal_coorperation", "Municipal Corporation"),
                    _sel("ward", "Ward"),
                    _sel("cor_district", "Correspondence District"),
                    _sel("cor_tehsil", "Correspondence Tehsil"),
                ],
            }
        ]
    }
    bound, _ = bind_kb_to_form(form, _as_live(APIS), "address")
    f = {x["id"]: x for x in bound["sections"][0]["fields"]}

    def parent(fid):
        return f[fid]["datasource"]["payload"]["ApiParams"]["ParentId"]

    assert parent("tehsil") == "district"
    assert parent("village") == "tehsil"
    assert parent("muncipal_coorperation") == "tehsil"
    assert parent("ward") == "muncipal_coorperation"
    assert f["ward"]["dependsOnAny"] == ["muncipal_coorperation"]
    # correspondence tehsil must hang off cor_district, not the permanent district
    assert parent("cor_tehsil") == "cor_district"
    assert f["cor_tehsil"]["dependsOnAny"] == ["cor_district"]
    # no placeholder may leak into a bound form
    for x in f.values():
        if "datasource" not in x:  # state/district come from the admin's own entries
            continue
        assert "{district}" not in str(x["datasource"])
        assert "{tehsil}" not in str(x["datasource"])
        assert "{municipality}" not in str(x["datasource"])