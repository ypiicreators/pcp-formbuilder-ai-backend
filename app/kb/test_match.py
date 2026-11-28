"""KB entry selection for prompts."""

from app.kb.match import select_kb_entries


def test_policy_with_field_types_only_is_always_included():
    entries = [
        {
            "key": "integer.values",
            "kind": "policy",
            "title": "Integer static options",
            "appliesTo": {
                "modes": [],
                "fieldTypes": ["select", "radio"],
                "keywords": [],
            },
        }
    ]
    picked = select_kb_entries(entries, "add gender dropdown", mode="generate")
    assert len(picked) == 1
    assert picked[0]["key"] == "integer.values"


def test_recommendation_and_bundle_are_skipped_for_prompts():
    entries = [
        {
            "key": "rec.missing_applying_for_yourself",
            "kind": "recommendation",
            "title": "Ask",
            "appliesTo": {
                "modes": [],
                "fieldTypes": [],
                "keywords": ["applying for yourself"],
            },
        },
        {
            "key": "bundle.self_vs_beneficiary_routing",
            "kind": "bundle",
            "title": "Bundle",
            "appliesTo": {
                "modes": [],
                "fieldTypes": [],
                "keywords": ["applying for yourself"],
            },
        },
    ]
    picked = select_kb_entries(
        entries, "add are you applying for yourself", mode="generate"
    )
    assert picked == []