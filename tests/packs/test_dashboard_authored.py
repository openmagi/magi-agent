from __future__ import annotations

import pytest

from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DASHBOARD_PACK_ID,
    DashboardCheck,
    DashboardTrigger,
    DashboardTriggerMatch,
)


def test_pack_constants_are_dashboard_namespaced() -> None:
    assert DASHBOARD_PACK_DIR_NAME == "dashboard-authored"
    assert DASHBOARD_PACK_ID == "ext.dashboard.checks"


def test_dashboard_check_minimum_valid_shape() -> None:
    check = DashboardCheck(
        id="ssn-leak",
        label="Block SSN leak from web_fetch",
        scope="always",
        enabled=True,
        trigger=DashboardTrigger(
            tool="web_fetch",
            match=DashboardTriggerMatch(pattern=r"\d{3}-\d{2}-\d{4}", is_regex=True),
        ),
        action="block",
    )
    assert check.id == "ssn-leak"
    assert check.trigger.match.is_regex is True


from magi_agent.packs.dashboard_authored import validate_dashboard_check


def _ok(**over):
    base = {
        "id": "ssn-leak",
        "label": "Block SSN",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": "web_fetch", "match": {"pattern": "ssn", "isRegex": False}},
        "action": "block",
    }
    base.update(over)
    return base


def test_validate_passes_minimal() -> None:
    assert validate_dashboard_check(_ok()) == []


def test_validate_rejects_uppercase_id() -> None:
    errs = validate_dashboard_check(_ok(id="SSN-leak"))
    assert any("id" in e for e in errs)


def test_validate_rejects_id_too_long() -> None:
    errs = validate_dashboard_check(_ok(id="a" * 64))
    assert any("id" in e for e in errs)


def test_validate_rejects_id_starts_with_hyphen() -> None:
    errs = validate_dashboard_check(_ok(id="-bad"))
    assert any("id" in e for e in errs)


def test_validate_rejects_newline_in_label() -> None:
    errs = validate_dashboard_check(_ok(label="line1\nline2"))
    assert any("label" in e for e in errs)


def test_validate_rejects_oversize_label() -> None:
    errs = validate_dashboard_check(_ok(label="x" * 201))
    assert any("label" in e for e in errs)


def test_validate_rejects_unknown_scope() -> None:
    errs = validate_dashboard_check(_ok(scope="universe"))
    assert any("scope" in e for e in errs)


def test_validate_rejects_unknown_action() -> None:
    errs = validate_dashboard_check(_ok(action="explode"))
    assert any("action" in e for e in errs)


def test_validate_rejects_empty_tool() -> None:
    rule = _ok(); rule["trigger"]["tool"] = ""
    assert any("tool" in e for e in validate_dashboard_check(rule))


def test_validate_rejects_empty_pattern() -> None:
    rule = _ok(); rule["trigger"]["match"]["pattern"] = ""
    assert any("pattern" in e for e in validate_dashboard_check(rule))


def test_validate_rejects_oversize_pattern() -> None:
    rule = _ok(); rule["trigger"]["match"]["pattern"] = "x" * 501
    assert any("pattern" in e for e in validate_dashboard_check(rule))


def test_validate_rejects_invalid_regex() -> None:
    rule = _ok(); rule["trigger"]["match"] = {"pattern": "([unclosed", "isRegex": True}
    assert any("regex" in e.lower() for e in validate_dashboard_check(rule))


def test_validate_rejects_catastrophic_regex() -> None:
    # Heuristic: nested quantifiers like (.+)+ are commonly catastrophic.
    rule = _ok(); rule["trigger"]["match"] = {"pattern": "(.+)+x", "isRegex": True}
    assert any("regex" in e.lower() for e in validate_dashboard_check(rule))


from magi_agent.packs.dashboard_authored import slug_of


def test_slug_of_simple_lowercased() -> None:
    assert slug_of("Block SSN leak") == "block-ssn-leak"


def test_slug_of_strips_non_alphanumeric() -> None:
    assert slug_of("API key!! (sensitive)") == "api-key-sensitive"


def test_slug_of_collision_suffix() -> None:
    existing = {"my-check"}
    assert slug_of("My check", taken=existing) == "my-check-2"


def test_slug_of_multiple_collisions() -> None:
    existing = {"x", "x-2", "x-3"}
    assert slug_of("X", taken=existing) == "x-4"


def test_slug_of_empty_label_falls_back() -> None:
    assert slug_of("!!!") == "check"
