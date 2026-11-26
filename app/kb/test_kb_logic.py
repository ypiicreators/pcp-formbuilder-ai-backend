"""Unit tests for KB derivations and validation."""

from app.kb.logic import pending_of, status_of
from app.kb.validate import validate_create, validate_key
from app.kb.schemas import KbAppliesTo, KbCreateInput, KbKind


def test_validate_key_rejects_uppercase():
    assert validate_key("Geo.State")


def test_pending_modified_when_live_behind_head():
    rec = {
        "key": "std.pincode",
        "title": "PIN",
        "kind": "template",
        "version": 2,
        "liveVersion": 1,
        "archived": False,
    }
    p = pending_of(rec)
    assert p is not None
    assert p.change.value == "modified"
    assert status_of(rec).value == "published"


def test_validate_create_policy_body_min_length():
    inp = KbCreateInput(
        key="policy.test_rule",
        kind=KbKind.policy,
        title="Test",
        body="too short",
        appliesTo=KbAppliesTo(),
        payload=None,
    )
    errs = validate_create(inp)
    assert any("20 characters" in e for e in errs)