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
