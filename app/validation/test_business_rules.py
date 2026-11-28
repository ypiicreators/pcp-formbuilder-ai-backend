"""L2 static option values: integers and YES/NO radios are allowed."""

from app.validation.validator import validate_form


def _form(*fields: dict) -> dict:
    return {
        "sections": [
            {
                "id": "s1",
                "title": {"en": "Applicant"},
                "fields": list(fields),
            }
        ]
    }


def test_yes_no_radio_is_valid():
    result = validate_form(
        _form(
            {
                "id": "apply_for_beneficiary",
                "type": "radio",
                "label": {"en": "Existing member?"},
                "options": [
                    {"value": "YES", "label": {"en": "YES"}},
                    {"value": "NO", "label": {"en": "NO"}},
                ],
            }
        )
    )
    assert result.ok, result.error_messages()


def test_integer_select_is_valid():
    result = validate_form(
        _form(
            {
                "id": "gender",
                "type": "select",
                "label": {"en": "Gender"},
                "options": [
                    {"value": 1, "label": {"en": "Male"}},
                    {"value": 2, "label": {"en": "Female"}},
                ],
            }
        )
    )
    assert result.ok, result.error_messages()


def test_male_female_strings_are_rejected():
    result = validate_form(
        _form(
            {
                "id": "gender",
                "type": "select",
                "label": {"en": "Gender"},
                "options": [
                    {"value": "MALE", "label": {"en": "Male"}},
                    {"value": "FEMALE", "label": {"en": "Female"}},
                ],
            }
        )
    )
    assert not result.ok
    assert any("MALE" in m for m in result.error_messages())
