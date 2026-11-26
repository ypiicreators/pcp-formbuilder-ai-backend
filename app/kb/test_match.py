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