"""KB-pinned field identity: resolve, skeleton, rename with references, safety net."""

from app.core.doc_spec_parser import SpecField, SpecSection
from app.core.spec_skeleton import build_spec_skeleton
from app.kb.identity import (
    apply_kb_field_ids,
    form_field_ids,
    rename_field_id,
    resolve_identity,
)

APPLYING_FOR = {
    "key": "std.applying_for_yourself",
    "kind": "template",
    "title": "Applying for yourself (fixed id)",
    "body": "",
    # fieldTypes is set on purpose: identity must ignore it (documents say "radio").
    "appliesTo": {
        "modes": [],
        "fieldTypes": ["applying_for"],
        "keywords": ["applying for yourself", "applying for self"],
    },
    "payload": {
        "id": "applying_for_yourself",
        "type": "applying_for",
        "options": [
            {"value": "YES", "label": {"en": "Yes"}},
            {"value": "NO", "label": {"en": "No"}},
        ],
    },
}


def test_resolve_identity_matches_label_regardless_of_source_type():
    pinned = resolve_identity([APPLYING_FOR], "Are you applying for yourself?", "radio")
    assert pinned["id"] == "applying_for_yourself"
    assert pinned["type"] == "applying_for"
    assert resolve_identity([APPLYING_FOR], "Applicant Full Name (English)", "text") is None


SELF_VS_BENEFICIARY = {
    "key": "policy.self_vs_beneficiary",
    "kind": "policy",
    "title": "Self vs beneficiary routing",
    "body": (
        "Use only when the service can be filed for someone other than the applicant.\n"
        "1. applying_for_yourself: type applying_for, id exactly applying_for_yourself,\n"
        "   options YES / NO (singleton).\n"
        "2. apply_for_beneficiary: radio YES/NO (\"existing profile member?\"), visibleWhen\n"
        "   applying_for_yourself = [\"NO\"].\n"
    ),
    "appliesTo": {
        "modes": [],
        "fieldTypes": [],
        "keywords": ["beneficiary", "someone else", "on behalf", "family member", "applying for"],
    },
    "payload": None,
}


def test_live_policy_body_pins_radio_document_field():
    """The published KB stores this as a policy with no payload; documents say 'radio'."""
    pinned = resolve_identity([SELF_VS_BENEFICIARY], "Are you applying for yourself?", "radio")
    assert pinned is not None
    assert pinned["id"] == "applying_for_yourself"
    assert pinned["type"] == "applying_for"
    assert [o["value"] for o in pinned["options"]] == ["YES", "NO"]
    # Generic "applying for" must not steal other fields.
    assert resolve_identity([SELF_VS_BENEFICIARY], "Are you applying for Aadhaar eKYC?", "radio") is None
    assert resolve_identity([SELF_VS_BENEFICIARY], "Select Beneficiary from existing member?", "radio") is None

    form, _, warnings = build_spec_skeleton(
        [
            SpecSection(
                number=1,
                title="Applicant Details",
                fields=[
                    SpecField(name="Are you applying for yourself?", field_type="radio", mandatory="Mandatory"),
                ],
            )
        ],
        identity=lambda label, ftype: resolve_identity([SELF_VS_BENEFICIARY], label, ftype),
    )
    field = form["sections"][0]["fields"][0]
    assert field["id"] == "applying_for_yourself"
    assert field["type"] == "applying_for"
    assert any("KB identity" in w for w in warnings)


APPLICANT_IDENTITY = {
    "key": "policy.applicant_identity",
    "kind": "policy",
    "title": "Applicant identity block",
    "body": (
        "Use the standard pair: applicant_name (text, English) + applicant_name_pa (text,\n"
        "Punjabi). Both required; the Punjabi field is filled by transliteration.\n"
        "Contact: mobile_number (phone) and email_id (email).\n"
    ),
    "appliesTo": {
        "modes": [],
        "fieldTypes": [],
        "keywords": ["applicant", "citizen", "mobile number", "email id"],
    },
    "payload": None,
}


def test_applicant_identity_policy_pins_full_name_english_to_applicant_name():
    """Documents say 'Applicant Full Name (English)'; KB id is applicant_name."""
    pinned = resolve_identity(
        [APPLICANT_IDENTITY], "Applicant Full Name (English)", "text"
    )
    assert pinned is not None
    assert pinned["id"] == "applicant_name"
    assert pinned["type"] == "text"
    assert "_" not in "".join(pinned.keys())  # internal _lang must not leak

    pa = resolve_identity([APPLICANT_IDENTITY], "Applicant Full Name (Punjabi)", "text")
    assert pa is not None
    assert pa["id"] == "applicant_name_pa"

    # Do not steal address / beneficiary names.
    assert resolve_identity([APPLICANT_IDENTITY], "Applicant Address (English)", "textarea") is None
    assert resolve_identity([APPLICANT_IDENTITY], "Beneficiary Full Name (English)", "text") is None
    # Running text like "mirror the applicant (autofill)" must not become an id.
    assert resolve_identity([SELF_VS_BENEFICIARY], "Applicant Address (English)", "textarea") is None

    form, _, warnings = build_spec_skeleton(
        [
            SpecSection(
                number=1,
                title="Applicant Details",
                fields=[
                    SpecField(name="Applicant Full Name (English)", field_type="text", mandatory="Mandatory"),
                    SpecField(name="Applicant Full Name (Punjabi)", field_type="text", mandatory="Mandatory"),
                ],
            )
        ],
        identity=lambda label, ftype: resolve_identity([APPLICANT_IDENTITY], label, ftype),
    )
    ids = [f["id"] for f in form["sections"][0]["fields"]]
    assert ids == ["applicant_name", "applicant_name_pa"]
    assert form["sections"][0]["fields"][1]["transliterationField"] == "applicant_name"
    assert any("applicant_name" in w for w in warnings)


def test_templates_without_an_id_are_not_identity_entries():
    mobile = {
        "key": "std.mobile",
        "kind": "template",
        "title": "Mobile",
        "body": "",
        "appliesTo": {"modes": [], "fieldTypes": ["phone"], "keywords": ["mobile"]},
        "payload": {"type": "phone"},
    }
    assert resolve_identity([mobile], "Mobile Number") is None


def test_skeleton_uses_kb_id_type_and_options_from_the_start():
    sections = [
        SpecSection(
            number=1,
            title="Applicant Details",
            fields=[
                SpecField(name="Are you applying for yourself?", field_type="radio", mandatory="Mandatory"),
                SpecField(
                    name="Relation with Beneficiary",
                    field_type="select",
                    mandatory="Mandatory",
                    dependency="Visible when Are you applying for Yourself? = NO.",
                ),
            ],
        )
    ]
    form, manifest, warnings = build_spec_skeleton(
        sections, identity=lambda label, ftype: resolve_identity([APPLYING_FOR], label, ftype)
    )
    first = form["sections"][0]["fields"][0]
    assert first["id"] == "applying_for_yourself"
    assert first["type"] == "applying_for"
    assert [o["value"] for o in first["options"]] == ["YES", "NO"]
    assert any("KB identity" in w for w in warnings)
    # Untouched fields keep their label-derived ids.
    assert form["sections"][0]["fields"][1]["id"] == "relation_with_beneficiary"

    without_kb, _, _ = build_spec_skeleton(sections)
    assert without_kb["sections"][0]["fields"][0]["id"] == "are_you_applying_for_yourself"


def test_rename_rewrites_references_but_not_human_text():
    form = {
        "sections": [
            {
                "id": "s",
                "fields": [
                    {"id": "are_you", "type": "radio", "label": {"en": "are_you"}},
                    {
                        "id": "member",
                        "type": "select",
                        "label": {"en": "Pick, if are_you is NO"},
                        "visibleWhen": {"are_you": ["NO"]},
                        "dependsOn": ["are_you"],
                        "requiredExpression": "are_you == 'NO' AND are_you_other == 1",
                    },
                ],
            }
        ],
        "crossFieldValidators": [{"id": "r", "expression": "NOT (are_you == 'YES')"}],
    }
    out = rename_field_id(form, "are_you", "applying_for_yourself")
    member = out["sections"][0]["fields"][1]
    assert out["sections"][0]["fields"][0]["id"] == "applying_for_yourself"
    assert member["visibleWhen"] == {"applying_for_yourself": ["NO"]}
    assert member["dependsOn"] == ["applying_for_yourself"]
    # Token-aware: `are_you_other` is a different identifier and must not change.
    assert member["requiredExpression"] == "applying_for_yourself == 'NO' AND are_you_other == 1"
    assert out["crossFieldValidators"][0]["expression"] == "NOT (applying_for_yourself == 'YES')"
    # Labels are human text and are left alone.
    assert member["label"]["en"] == "Pick, if are_you is NO"


def test_safety_net_renames_merges_template_and_skips_conflicts():
    def form_with(*ids):
        return {
            "sections": [
                {
                    "id": "s",
                    "fields": [
                        {"id": i, "type": "radio", "label": {"en": "Are you applying for yourself?"}}
                        for i in ids
                    ],
                }
            ]
        }

    fixed, warnings = apply_kb_field_ids(form_with("are_you_applying_for_yourself"), [APPLYING_FOR])
    field = fixed["sections"][0]["fields"][0]
    assert field["id"] == "applying_for_yourself"
    assert field["type"] == "applying_for"  # template enforced even though fieldTypes differ
    assert field["kbKey"] == "std.applying_for_yourself"
    assert any("renamed" in w for w in warnings)

    # Singleton: a second matching field must not become a duplicate id.
    two, _ = apply_kb_field_ids(form_with("a1", "a2"), [APPLYING_FOR])
    ids = [f["id"] for f in two["sections"][0]["fields"]]
    assert ids == ["applying_for_yourself", "a2"]

    # An id already on the canvas (append mode) is reserved.
    kept, w3 = apply_kb_field_ids(
        form_with("are_you_applying_for_yourself"),
        [APPLYING_FOR],
        reserved_ids={"applying_for_yourself"},
    )
    assert form_field_ids(kept) == {"are_you_applying_for_yourself"}
    assert any("already used" in w for w in w3)
