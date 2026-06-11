"""Tests for the ``MAGI_GA_LIVE_ENABLED`` flag (``general_automation_live_enabled``).

The GA-live readiness gate module (``magi_agent/gates/ga_live_readiness.py``,
Track 19 PR4) was deleted in the promote-or-delete sweep — it had zero non-test
importers. The env flag it projected remains live code in
``magi_agent/config/env.py`` and is consumed by ``harness/general_automation``
and ``adk_bridge/control_plane``; those flag-resolution behaviors stay covered
here.
"""
from __future__ import annotations

import pytest


def test_ga_live_enabled_flag_default_on_in_full_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAGI_GA_LIVE_ENABLED absent → True in the local full runtime profile."""
    monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    from magi_agent.config.env import general_automation_live_enabled
    assert general_automation_live_enabled() is True


def test_ga_live_enabled_flag_safe_profile_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    from magi_agent.config.env import general_automation_live_enabled
    assert general_automation_live_enabled() is False


def test_ga_live_enabled_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    from magi_agent.config.env import general_automation_live_enabled
    assert general_automation_live_enabled() is True


def test_ga_live_enabled_flag_truthy_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.config.env import general_automation_live_enabled
    for val in ("true", "yes", "on", "1"):
        monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", val)
        assert general_automation_live_enabled() is True, f"Expected True for {val!r}"
