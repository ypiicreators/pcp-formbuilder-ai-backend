"""showTotal (Show Total Sum) — prompt contracts + validation + edit apply."""

from app.core.change_set import apply_change_set, parse_change_set
from app.core.compaction import compact_form
from app.prompts.constants import render_feature_contracts
from app.prompts.system_prompt import build_system_prompt
from app.prompts.user_prompts import build_edit_prompt, build_nl_prompt
from app.validation.validator import validate_form


def _table_form(*, column_type: str = "number", show_total: bool | None = True) -> dict:
    col: dict = {
        "id": "income",
        "type": column_type,
        "label": {"en": "Income"},
        "required": True,
    }
    if show_total is not None:
        col["showTotal"] = show_total
    return {
        "sections": [
            {
                "id": "family",
                "title": {"en": "Family"},
                "fields": [
                    {
                        "id": "members",
                        "type": "table",
                        "label": {"en": "Members"},
                        "minRows": 1,
                        "maxRows": 10,
                        "rowKey": "row_id",
                        "rowKeyStrategy": "uuid",
                        "columns": [
                            {
                                "id": "name",
                                "type": "text",
                                "label": {"en": "Name"},
                            },
                            col,
                        ],
                    }
                ],
            }
        ]
    }


def test_feature_contract_documents_show_total_opt_in_only():
    text = render_feature_contracts()
    assert "showTotal" in text
    assert "Show Total Sum" in text
    assert "ONLY when the requirement" in text
    assert '"showTotal": true' in text
    assert "did not ask for a sum/total of that column" in text


def test_system_prompt_includes_show_total_on_table_column():
    prompt = build_system_prompt()
    assert '"showTotal"?: boolean' in prompt
    assert "ONLY when the requirement explicitly" in prompt


def test_nl_prompt_mentions_show_total_when_sum_requested():
    prompt = build_nl_prompt(
        "table with income column and total sum of income"
    )
    assert "showTotal" in prompt


def test_edit_prompt_mentions_show_total_set_column_prop():
    prompt = build_edit_prompt(
        context={"sections": []},
        instruction="tick Show Total Sum on the income column",
    )
    assert "showTotal" in prompt
    assert "setColumnProp" in prompt


def test_compact_marks_show_total_on_number_column():
    compact = compact_form(_table_form(show_total=True))
    cols = compact["sections"][0]["fields"][0]["columns"]
    income = next(c for c in cols if c["id"] == "income")
    assert "showTotal" in income.get("features", [])


def test_validate_show_total_on_non_number_column_fails():
    form = _table_form(column_type="text", show_total=True)
    result = validate_form(form)
    assert not result.ok
    assert any(e.code == "show_total_on_non_number" for e in result.errors)


def test_validate_show_total_on_number_column_ok():
    form = _table_form(column_type="number", show_total=True)
    result = validate_form(form)
    assert result.ok, result.error_messages()


def test_validate_show_total_on_table_field_fails():
    form = _table_form(show_total=None)
    form["sections"][0]["fields"][0]["showTotal"] = True
    result = validate_form(form)
    assert not result.ok
    assert any(e.code == "column_flag_on_table" for e in result.errors)


def test_set_column_prop_show_total():
    form = _table_form(show_total=None)
    parsed = parse_change_set(
        [
            {
                "op": "setColumnProp",
                "tableId": "members",
                "fieldId": "income",
                "property": "showTotal",
                "value": True,
            }
        ]
    )
    assert parsed.ok, parsed.errors
    applied = apply_change_set(form, parsed.ops)
    assert applied.ok, applied.errors
    income = applied.form["sections"][0]["fields"][0]["columns"][1]
    assert income["showTotal"] is True
