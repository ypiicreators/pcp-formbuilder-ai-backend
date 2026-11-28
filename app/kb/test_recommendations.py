"""KB follow-up questions insert field templates."""

from app.kb.core_pack import CORE_PACK, FOLLOW_UPS, TEMPLATES
from app.kb.match import select_kb_entries
from app.kb.recommendations import (
    apply_followup_choice,
    apply_templates,
    clarification_options,
    first_recommendation,
    parse_followup_choice,
)
from app.kb.validate import validate_create


def _live(entries):
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


def test_core_pack_followup_entries_validate():
    for e in FOLLOW_UPS:
        assert validate_create(e) == [], (e.key, validate_create(e))


def test_recommendations_are_not_injected_into_llm_prompt():
    picked = select_kb_entries(
        _live(CORE_PACK),
        "create a form with applying for yourself",
        mode="generate",
    )
    kinds = {e["kind"] for e in picked}
    assert "recommendation" not in kinds
    assert "bundle" not in kinds


def test_missing_gate_matches_recommendation():
    form = {
        "sections": [
            {
                "id": "s1",
                "title": {"en": "Applicant"},
                "fields": [
                    {"id": "applicant_name", "type": "text", "label": {"en": "Name"}},
                ],
            }
        ]
    }
    rec = first_recommendation(form, _live(FOLLOW_UPS))
    assert rec is not None
    assert rec["key"] == "rec.missing_applying_for_yourself"
    opts = clarification_options(rec)
    assert [o["field_id"] for o in opts] == [
        "kbrec|rec.missing_applying_for_yourself|yes",
        "kbrec|rec.missing_applying_for_yourself|no",
    ]


def test_present_gate_by_type_other_id_does_not_match():
    form = {
        "sections": [
            {
                "id": "s1",
                "fields": [
                    {
                        "id": "self_or_other",
                        "type": "applying_for",
                        "label": {"en": "Self?"},
                    }
                ],
            }
        ]
    }
    assert first_recommendation(form, _live(FOLLOW_UPS)) is None


def test_present_gate_does_not_match():
    form = {
        "sections": [
            {
                "id": "s1",
                "fields": [
                    {
                        "id": "applying_for_yourself",
                        "type": "applying_for",
                        "label": {"en": "Self?"},
                    }
                ],
            }
        ]
    }
    assert first_recommendation(form, _live(FOLLOW_UPS)) is None


def test_parse_prefers_no_when_both_selected():
    prompt = (
        "Selected: kbrec|rec.missing_applying_for_yourself|yes, "
        "kbrec|rec.missing_applying_for_yourself|no"
    )
    assert parse_followup_choice(prompt) == (
        "rec.missing_applying_for_yourself",
        "no",
    )


def test_apply_templates_inserts_gate_and_no_branch():
    form = {
        "sections": [
            {
                "id": "whom_you_are_applying_for",
                "title": {"en": "Applicant Details"},
                "fields": [
                    {"id": "applicant_name", "type": "text", "label": {"en": "Name"}},
                    {
                        "id": "applicant_name_pa",
                        "type": "text",
                        "label": {"en": "Name PA"},
                    },
                    {"id": "email_id", "type": "email", "label": {"en": "Email"}},
                ],
            }
        ]
    }
    keys = [
        "std.applying_for_yourself",
        "std.apply_for_beneficiary",
        "std.profile_member",
        "std.relation_with_beneficiary",
    ]
    out, warnings = apply_templates(form, _live(TEMPLATES), keys)
    ids = [f["id"] for f in out["sections"][0]["fields"]]
    assert ids.index("applicant_name_pa") < ids.index("applying_for_yourself")
    assert ids.index("applying_for_yourself") < ids.index("email_id")
    assert "apply_for_beneficiary" in ids
    assert "profile_member" in ids
    assert "relation_with_beneficiary" in ids
    gate = next(f for f in out["sections"][0]["fields"] if f["id"] == "applying_for_yourself")
    assert gate["type"] == "applying_for"
    assert [o["value"] for o in gate["options"]] == ["YES", "NO"]
    member = next(f for f in out["sections"][0]["fields"] if f["id"] == "apply_for_beneficiary")
    assert member["visibleWhen"] == {"applying_for_yourself": ["NO"]}
    assert any("Added 4 field" in w for w in warnings)


def test_yes_choice_inserts_templates():
    form = {"sections": [{"id": "s", "title": {"en": "S"}, "fields": []}]}
    updated, warnings, err = apply_followup_choice(
        form,
        _live(TEMPLATES + FOLLOW_UPS),
        ("rec.missing_applying_for_yourself", "yes"),
    )
    assert err is None
    ids = {f["id"] for f in updated["sections"][0]["fields"]}
    assert "applying_for_yourself" in ids
    assert "relation_with_beneficiary" in ids
    assert warnings


def test_no_choice_leaves_form():
    form = {
        "sections": [
            {
                "id": "s",
                "fields": [{"id": "email_id", "type": "email", "label": {"en": "E"}}],
            }
        ]
    }
    updated, warnings, err = apply_followup_choice(
        form,
        _live(FOLLOW_UPS),
        ("rec.missing_applying_for_yourself", "no"),
    )
    assert err is None
    assert [f["id"] for f in updated["sections"][0]["fields"]] == ["email_id"]
    assert warnings
