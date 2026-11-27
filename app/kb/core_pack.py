"""Core Knowledge Base pack for Digi Punjab citizen forms.

Distilled from `pattern/` (617 active service schemas): only the recurring business
modules, not every field. Observed corpus patterns are written as rules; the few
recommended (not observed) rules are labelled "recommended".

Install as DRAFTS (nothing goes live until an admin publishes a release):

    .venv/bin/python -m app.kb.core_pack

Existing keys are skipped, so it is safe to re-run and never overwrites admin edits.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.kb.schemas import KbAppliesTo, KbCreateInput, KbKind

_GEO_URL = "api/um/GeoUnit/GetGeoUnitDList"


def _geo(parent: str | int, geo_type_id: int) -> dict[str, Any]:
    """GeoUnit datasource in the production envelope. `{role}` = parent field id."""
    return {
        "url": _GEO_URL,
        "method": "POST",
        "payload": {
            "ApiKey": "",
            "OrgId": 1,
            "ApiParams": {"ParentId": parent, "GeoTypeId": geo_type_id},
            "ResponseId": 0,
            "Path": "",
            "Module": "",
            "UserId": 0,
            "UserDetails": "",
            "UserSessionModel": "",
            "Language": "",
            "FormAction": 0,
        },
    }


def _entry(
    key: str,
    kind: KbKind,
    title: str,
    body: str,
    keywords: list[str],
    *,
    field_types: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> KbCreateInput:
    return KbCreateInput(
        key=key,
        kind=kind,
        title=title,
        body=body.strip(),
        appliesTo=KbAppliesTo(
            modes=[], fieldTypes=field_types or [], keywords=keywords
        ),
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# Policies — one per business module
# --------------------------------------------------------------------------- #

POLICIES: list[KbCreateInput] = [
    _entry(
        "policy.applicant_identity",
        KbKind.policy,
        "Applicant identity block",
        """
Every citizen service starts with the applicant (the logged-in citizen).
- Use the standard pair: applicant_name (text, English) + applicant_name_pa (text,
  Punjabi). Both required; the Punjabi field is filled by transliteration from the
  English one (transliteration + transliterationField), not a separate free input.
- Name rules: English 3-50 letters with single spaces only; Punjabi in Gurmukhi only.
- Locale pairs (always, even if the document only lists English): whenever the form
  has applicant_name, father_name (text, English), mother_name (text, English),
  spouse_name (text, English), or applicant_address (textarea, English), also emit
  the matching _pa sibling immediately after it. Do not wait for the document to
  mention Punjabi.
- Contact: mobile_number (phone) and email_id (email); normally filled from the
  citizen profile (see the profile autofill policy).
- Do not add extra applicant fields (father, address, etc.) unless the request asks
  or the uploaded spec lists them.
- Never rename an existing field id; ids are backend contract keys.
""",
        ["applicant", "citizen", "mobile number", "email id"],
    ),
    _entry(
        "policy.self_vs_beneficiary",
        KbKind.policy,
        "Self vs beneficiary routing",
        """
Use only when the service can be filed for someone other than the applicant.
1. applying_for_yourself: type applying_for, id exactly applying_for_yourself,
   options YES / NO (singleton).
2. apply_for_beneficiary: radio YES/NO ("existing profile member?"), visibleWhen
   applying_for_yourself = ["NO"].
3. profile_member: select from the profile-members API (ref.profile_member_api),
   visibleWhen applying_for_yourself = ["NO"] AND apply_for_beneficiary = ["YES"].
4. relation_with_beneficiary: select from the relation API (ref.relation_api),
   visibleWhen applying_for_yourself = ["NO"] AND apply_for_beneficiary = ["NO"].
5. beneficiary_* fields: for self they mirror the applicant (autofill); otherwise
   they come from the chosen member or are entered manually.
If the service is only ever for the citizen, skip this routing and the beneficiary_*
fields entirely. Hidden branch values must not be submitted.
""",
        [
            "beneficiary",
            "someone else",
            "on behalf",
            "family member",
            "applying for",
            "relation with",
        ],
    ),
    _entry(
        "policy.beneficiary_family_details",
        KbKind.policy,
        "Beneficiary and family details",
        """
Beneficiary identity (when the form has a beneficiary):
- beneficiary_salutation -> beneficiary_gender -> marital_status, in that dependency
  order (each narrows the next via the relation-mapping / config reference lists).
- beneficiary_name + beneficiary_name_pa, beneficiary_dob, beneficiary_age
  (age derived from the DOB, not typed).
- spouse_name (+ _pa) is conditional: visibleWhen marital_status is a married value;
  never always visible.
Family: father_name/_pa and mother_name/_pa, each English field with a Punjabi twin
by transliteration. Guardian fields exist only when the beneficiary can be a minor
(visibleWhen beneficiary_is_minor = YES; must be empty for adults).
Relation must be consistent with salutation/gender (e.g. "Father" cannot carry a
female salutation) - add a cross-field rule if the service needs it.
Option values for these lists come from the reference APIs; do not hardcode them.
""",
        [
            "dob",
            "date of birth",
            "gender",
            "salutation",
            "marital",
            "spouse",
            "father",
            "mother",
            "guardian",
            "minor",
        ],
    ),
    _entry(
        "policy.address_permanent_correspondence",
        KbKind.policy,
        "Permanent and correspondence addresses",
        """
Two separate address groups; never merge them.
Permanent: permanent_address (line 1, required), permanent_address_2 (line 2,
optional), each with a Punjabi twin (_pa) by transliteration, then region_type,
state, district, tehsil, village or ward (see the geography policy), pin_code.
Correspondence uses the cor_ prefix: correspondence_address_1/_2, cor_state,
cor_district, cor_tehsil, cor_region, cor_village, cor_ward, cor_pin_code, plus a
YES/NO same_as_permanent_address control.
- Same-as = YES: copy ALL address, geography and PIN fields together (one logical
  group) and hide or lock the cor_* group; NO: cor_* fields are required and empty.
- Line 1 must differ from line 2 (crossFieldValidators: line1 == line2 -> error).
- PIN code: 6 digits, first digit not 0 (use the std.pincode template).
- Outside Punjab (state is not Punjab, id 242) show free-text town/village instead
  of the Punjab dropdowns.
Only ask for a correspondence address if the service needs one.
""",
        [
            "address",
            "permanent",
            "correspondence",
            "residence",
            "pin code",
            "pincode",
            "same as",
        ],
    ),
    _entry(
        "policy.geography_urban_rural",
        KbKind.policy,
        "Punjab geography cascade and urban/rural branching",
        """
Geography is a strict cascade of selects, each fed by the GeoUnit API (see the geo.*
Dropdown API entries) and depending on its parent:
state -> district -> tehsil -> Rural: village | Urban: municipal body -> ward.
Block / gram panchayat / patwar circle only when the service asks for them.
- Each child lists its parent in dependsOn / dependsOnAny; the datasource ParentId is
  the PARENT FIELD ID. Changing a parent must reset all descendants.
- region_type is a radio with two static options (Rural, Urban) and integer values
  (1 = Rural, 2 = Urban). village is visibleWhen region_type = [1]; municipal body
  and ward are visibleWhen region_type = [2]. For the correspondence group use
  cor_region and the cor_ ids the same way.
- Never use one static list for states/districts/tehsils; always the API.
- Never add region_type when the service has no village/ward level.
""",
        [
            "state",
            "district",
            "tehsil",
            "village",
            "ward",
            "municipal",
            "urban",
            "rural",
            "region",
            "geography",
        ],
    ),
    _entry(
        "policy.autofill_profile_aadhaar_ocr",
        KbKind.policy,
        "Profile, Aadhaar eKYC and OCR autofill",
        """
Every autofill must state its source, lock behaviour and manual-override policy.
Recommended precedence: Aadhaar eKYC > OCR > citizen profile > manual entry.
- Profile: autofillFromProfile = profile key, e.g. Name.en -> applicant_name,
  MobileNo -> mobile_number, Email -> email_id, PinCode -> pin_code,
  AadhaarAddress.en -> applicant_address, DistrictMeta -> district, TehsilMeta ->
  tehsil, Gender -> gender, FatherName.en -> father_name. Lock identity fields
  (lockAfterProfileAutofill true); keep address/contact editable.
- Aadhaar eKYC: place applicant_aadhaar_ekyc / beneficiary_aadhaar_ekyc, and set
  source (the eKYC TYPE, not a field id) + autoFillFormAadharEkyc (Name, DOB,
  Gender, Pincode...) + lockAfterAutoFill on the plain inputs. For someone else,
  gate beneficiary eKYC behind applying_for_aadhaar_ekyc = YES.
- OCR: the file field needs OCR true + doc_type; text/date fields use
  autofillFromOCR = that file field's id.
- Never use autoFillWhen form_load for profile data; that is only for static
  defaults (e.g. Punjab state id 242).
""",
        [
            "autofill",
            "auto fill",
            "prefill",
            "pre-fill",
            "profile",
            "aadhaar",
            "ekyc",
            "ocr",
            "lock",
        ],
    ),
    _entry(
        "policy.service_eligibility",
        KbKind.policy,
        "Service eligibility and cross-field rules",
        """
Eligibility and consistency are rules, not free text. Use visibleWhen /
requiredExpression for "required only when", and crossFieldValidators (type custom,
expression, errorMessage with en + pa) for "reject when". Add only what the service
needs. Recurring families:
- Age and chronology: child age above the service limit blocked; registration date
  not before the birth/event date; date_from < date_to (validity, visa);
  years_of_residence <= beneficiary_age.
- Relationship: relation vs salutation/gender must agree.
- Identity: beneficiary eKYC required when applying for someone else and opting
  for Aadhaar eKYC.
- Previous record: when a prior record is claimed (record_available = YES) require
  its number and date; a chosen proof type requires its own number field.
Every field id used in an expression must exist on the form. Thresholds (ages,
years, limits) must come from the request; never invent a number.
""",
        [
            "eligib",
            "age limit",
            "minimum age",
            "maximum age",
            "years of residence",
            "previous record",
            "cross field",
            "must not be",
        ],
    ),
    _entry(
        "policy.documents",
        KbKind.policy,
        "Document requirements and uploads",
        """
Group documents in one section. For each person needing proof use a proof-type
select plus a file upload:
- Names: applicant_identity_proof_type, beneficiary_identity_proof_type,
  beneficiary_address_proof_type (each with its matching file field).
- The proof-type select is a Dropdown API from the DMS document-type endpoint
  (docs.identity_proof_types for identity). For address / other categories use the
  same endpoint but do NOT guess the CategoryId; leave a TODO warning instead.
- Every upload is type file with allowedTypes (.pdf .jpg .jpeg .png), maxSizeMB
  (default 5), maxFiles (default 1) and a DocumentType label. Mandatory documents
  are required; "other document" is optional.
- Conditional documents use visibleWhen on the controlling field.
- Aadhaar card upload: OCR true + doc_type "aadhaar".
- Use the flat keys allowedTypes / maxSizeMB, not validators fileType / maxSize.
""",
        [
            "document",
            "upload",
            "proof",
            "attachment",
            "certificate copy",
            "identity proof",
            "address proof",
        ],
    ),
    _entry(
        "policy.correction_workflow",
        KbKind.policy,
        "Correction workflow (original vs corrected values)",
        """
For correction / amendment services:
- Keep the ORIGINAL field (e.g. father_first_name) read-only and add a sibling with
  the suffix _corrected (father_first_name_corrected, mother_first_name_pa_corrected).
  Never overwrite or reuse the original id.
- One section per part being corrected (child / father / mother), each gated by a
  YES/NO toggle (correction_needed_child_section, correction_needed_father_section,
  correction_needed_mother_section). Corrected fields are visibleWhen the toggle is
  YES and required when visible.
- Each corrected English field has a Punjabi twin (..._pa_corrected) via
  transliteration.
- Every corrected part needs a supporting document upload (see the documents policy).
- Recommended: a cross-field rule that corrected != original, so a correction
  actually changes something.
""",
        [
            "correction",
            "correct the",
            "amend",
            "rectif",
            "to be corrected",
            "corrected",
        ],
    ),
    _entry(
        "policy.declaration_consent",
        KbKind.policy,
        "Declaration and consent",
        """
Close every citizen form with ONE declaration section as the LAST section, with no
inputs after it.
- A required checkbox with en + pa declaration text: id declaration_accepted
  (checkbox_declaration exists in older forms; do not rename existing ones).
- Add consents only when relevant: aadhaar_consent when Aadhaar eKYC is used,
  dpdp_consent for data-protection consent, applicant_declaration_consent and
  beneficiary_declaration_consent when applicant and beneficiary differ.
- The form must not be submittable unless the declaration is ticked.
- Use verify_declaration ({ aadhaar_ekyc?, declaration? }) only when the service
  asks for the declaration to be verified.
""",
        [
            "declaration",
            "consent",
            "undertaking",
            "i agree",
            "terms",
            "self declaration",
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Dropdown APIs — approved datasources (bound after the LLM responds)
# Placeholders {district} / {tehsil} / {municipality} resolve to the parent field id.
# --------------------------------------------------------------------------- #

_SELECT = ["select"]

APIS: list[KbCreateInput] = [
    _entry(
        "geo.tehsil_api",
        KbKind.api_endpoint,
        "Tehsil dropdown (child of district)",
        "Tehsil list for the chosen district. GeoTypeId 13.",
        ["tehsil"],
        field_types=_SELECT,
        payload=_geo("{district}", 13),
    ),
    _entry(
        "geo.village_api",
        KbKind.api_endpoint,
        "Village dropdown (rural, child of tehsil)",
        "Village list for the chosen tehsil. GeoTypeId 15. Rural branch only.",
        ["village"],
        field_types=_SELECT,
        payload=_geo("{tehsil}", 15),
    ),
    _entry(
        "geo.municipality_api",
        KbKind.api_endpoint,
        "Municipal body dropdown (urban, child of tehsil)",
        "Municipal corporation / council list for the chosen tehsil. GeoTypeId 14. "
        "Urban branch only.",
        ["muncipal", "municipal"],
        field_types=_SELECT,
        payload=_geo("{tehsil}", 14),
    ),
    _entry(
        "geo.ward_api",
        KbKind.api_endpoint,
        "Ward dropdown (urban, child of municipal body)",
        "Ward list for the chosen municipal body. GeoTypeId 74. Urban branch only.",
        ["ward"],
        field_types=_SELECT,
        payload=_geo("{municipality}", 74),
    ),
    _entry(
        "ref.relation_api",
        KbKind.api_endpoint,
        "Relation with beneficiary",
        "Relationship list (RefType Relation) for relation_with_beneficiary.",
        ["relation_with_beneficiary", "relation with beneficiary"],
        field_types=_SELECT,
        payload={
            "url": "api/publicservice/Config/GetConfigtDList",
            "method": "POST",
            "payload": {"ApiKey": "", "OrgId": 0, "ApiParams": {"RefType": "Relation"}},
        },
    ),
    _entry(
        "ref.profile_member_api",
        KbKind.api_endpoint,
        "Existing profile / family members",
        "Members saved on the citizen's profile; used by profile_member when the "
        "beneficiary is an existing member.",
        ["profile_member", "profile member"],
        field_types=_SELECT,
        payload={
            "url": "api/authentication/CitizenContoller/GetMemberDListByMobileNumber",
            "method": "POST",
            "payload": {"ApiKey": "", "OrgId": 0, "ApiParams": {}},
        },
    ),
    _entry(
        "docs.identity_proof_types",
        KbKind.api_endpoint,
        "Identity proof document types",
        "Document types for identity proof selects. CategoryId 5 is the identity "
        "category observed in production forms.",
        ["identity_proof_type", "identity proof"],
        field_types=_SELECT,
        payload={
            "url": "api/dms/ServiceDoc/GetDocTypeListByCategoryList",
            "method": "POST",
            "payload": {
                "SearchText": "",
                "CurrentPageNumber": 0,
                "PageSize": 0,
                "FilterType": 0,
                "SortBy": "",
                "SortOrder": "",
                "FilterParams": {"CategoryId": 5},
                "Language": "",
                "ExportFilterType": 0,
                "ExportType": 0,
                "AppliedFilter": "",
            },
        },
    ),
]


# --------------------------------------------------------------------------- #
# Field templates — canonical validation profiles (pattern/validation-profiles.json)
# --------------------------------------------------------------------------- #

TEMPLATES: list[KbCreateInput] = [
    _entry(
        "std.applying_for_yourself",
        KbKind.template,
        'Applying for yourself (fixed id)',
        'The single "Are you applying for yourself?" control: type applying_for, '
        'id exactly applying_for_yourself, options YES / NO. A template with an "id" '
        "pins the field id wherever the form is generated from a document.",
        ["applying for yourself", "applying for self", "apply for yourself"],
        # No field-type filter on purpose: a source document may call it "radio".
        payload={
            "id": "applying_for_yourself",
            "type": "applying_for",
            "options": [
                {"value": "YES", "label": {"en": "Yes"}},
                {"value": "NO", "label": {"en": "No"}},
            ],
        },
    ),
    _entry(
        "std.pincode",
        KbKind.template,
        "PIN code (INDIA_PINCODE)",
        "Exactly six digits; first digit cannot be zero.",
        ["pin_code", "pincode", "pin code"],
        field_types=["pincode"],
        payload={
            "type": "pincode",
            "validators": [
                {"type": "maxLength", "value": 6},
                {
                    "type": "regex",
                    "pattern": "^[1-9][0-9]{5}$",
                    "message": "Invalid PIN code",
                },
            ],
        },
    ),
    _entry(
        "std.mobile",
        KbKind.template,
        "Mobile number (INDIA_MOBILE)",
        "Ten digits starting 6-9.",
        ["mobile_number", "mobile number", "mobile"],
        field_types=["phone"],
        payload={
            "type": "phone",
            "validators": [
                {"type": "minLength", "value": 10},
                {"type": "maxLength", "value": 10},
                {
                    "type": "regex",
                    "pattern": "^[6-9][0-9]{9}$",
                    "message": "Invalid mobile number",
                },
            ],
        },
    ),
]


CORE_PACK: list[KbCreateInput] = POLICIES + APIS + TEMPLATES


async def install(dry_run: bool = False) -> dict[str, list[str]]:
    """Create missing entries as drafts. Returns {created, skipped}."""
    from app.db import mongo
    from app.kb import repository as repo
    from app.kb import service

    result: dict[str, list[str]] = {"created": [], "skipped": []}
    if dry_run:
        result["created"] = [e.key for e in CORE_PACK]
        return result

    await mongo.connect()
    try:
        db = mongo.get_db()
        await repo.ensure_indexes(db)
        for entry in CORE_PACK:
            if await repo.get_entry(db, entry.key):
                result["skipped"].append(entry.key)
                continue
            await service.create_entry(db, entry, actor="core-pack")
            result["created"].append(entry.key)
    finally:
        await mongo.close()
    return result


def main() -> None:
    import sys

    out = asyncio.run(install(dry_run="--dry-run" in sys.argv))
    print(f"created ({len(out['created'])}):", *out["created"], sep="\n  ")
    print(f"skipped, already exist ({len(out['skipped'])}):", *out["skipped"], sep="\n  ")


if __name__ == "__main__":
    main()
