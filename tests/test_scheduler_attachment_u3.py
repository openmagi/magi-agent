"""U3 -- scheduler attachment seam.

TDD: when MAGI_SCHEDULER_EXECUTOR_ENABLED is set, build_default_watchers() /
build_local_scheduler_cron_driver() sets MAGI_SCHEDULER_ATTACHED in the process
environment so native cron tools route past the honest-block to the live store.

Without the attachment seam the cron tools always return cron_not_configured;
with it they accept CronCreate calls.

P1-2 env hygiene: all env manipulation uses pytest monkeypatch so changes are
rolled back between tests.  attach_local_scheduler() itself also refuses to act
when MAGI_SCHEDULER_EXECUTOR_ENABLED is not set, preventing accidental env
pollution when called without the gate.
"""
from __future__ import annotations

import os
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Attachment seam: attach_local_scheduler sets MAGI_SCHEDULER_ATTACHED
# ---------------------------------------------------------------------------

def test_attach_local_scheduler_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """attach_local_scheduler() sets MAGI_SCHEDULER_ATTACHED when the executor gate is on."""
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)

    from magi_agent.gateway.watchers import attach_local_scheduler

    attach_local_scheduler()
    assert os.environ.get("MAGI_SCHEDULER_ATTACHED") in {"1", "true", "True"}


def test_attach_local_scheduler_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling attach twice must not raise and env stays set."""
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)

    from magi_agent.gateway.watchers import attach_local_scheduler

    attach_local_scheduler()
    attach_local_scheduler()
    assert os.environ.get("MAGI_SCHEDULER_ATTACHED")


def test_attach_local_scheduler_refuses_without_executor_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2: attach_local_scheduler() must NOT set MAGI_SCHEDULER_ATTACHED when
    MAGI_SCHEDULER_EXECUTOR_ENABLED is off (the gate is now enforced inside the
    function, not just in callers)."""
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)

    from magi_agent.gateway.watchers import attach_local_scheduler

    attach_local_scheduler()
    # Must NOT have been set -- gate is enforced.
    assert not os.environ.get("MAGI_SCHEDULER_ATTACHED")


# ---------------------------------------------------------------------------
# Before attachment: cron_create returns cron_not_configured
# ---------------------------------------------------------------------------

def test_cron_create_blocked_without_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)
    # Force native_receipts_honest so the honest block fires.
    monkeypatch.setenv("MAGI_NATIVE_RECEIPTS_HONEST", "1")

    from magi_agent.plugins.native.scheduled_work import cron_create
    from magi_agent.tools.context import ToolContext

    ctx = ToolContext(bot_id="bot:u3", session_id="session:u3")
    result = cron_create({"schedule": "0 * * * *", "task": "hello"}, ctx)

    # The honest block fires: status="blocked", error_code="cron_not_configured".
    assert result.status == "blocked"
    assert "cron_not_configured" in (result.error_code or "")


# ---------------------------------------------------------------------------
# After attachment: cron_create is no longer blocked
# ---------------------------------------------------------------------------

def test_cron_create_allowed_after_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SCHEDULER_ATTACHED", "1")
    monkeypatch.setenv("MAGI_NATIVE_RECEIPTS_HONEST", "1")

    from magi_agent.plugins.native.scheduled_work import cron_create
    from magi_agent.tools.context import ToolContext

    ctx = ToolContext(bot_id="bot:u3", session_id="session:u3")
    result = cron_create({"schedule": "0 * * * *", "task": "hello"}, ctx)

    # Not blocked -- the tool returns a non-blocked result.
    assert result.status != "blocked"


# ---------------------------------------------------------------------------
# P1-4: cron tools stay honest-blocked after importing gateway watchers
# with the executor flag DISABLED
# ---------------------------------------------------------------------------

def test_cron_tools_stay_blocked_after_importing_watchers_with_executor_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """P1-4: cron_create and cron_list must remain honest-blocked even after
    build_default_watchers() is called with MAGI_SCHEDULER_EXECUTOR_ENABLED=0.

    Regression guard: importing/building watchers with the executor gate off
    must not set MAGI_SCHEDULER_ATTACHED.
    """
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)
    monkeypatch.setenv("MAGI_NATIVE_RECEIPTS_HONEST", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.delenv("MAGI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_SLACK_BOT_TOKEN", raising=False)

    from magi_agent.gateway.watchers import build_default_watchers

    build_default_watchers()

    # The env must NOT have been poisoned.
    assert not os.environ.get("MAGI_SCHEDULER_ATTACHED"), (
        "MAGI_SCHEDULER_ATTACHED was set even though the executor gate is off"
    )

    # Native cron tools must still honest-block.
    from magi_agent.plugins.native.scheduled_work import cron_create, cron_list
    from magi_agent.tools.context import ToolContext

    ctx = ToolContext(bot_id="bot:p14", session_id="session:p14")
    create_result = cron_create({"schedule": "0 * * * *", "task": "p1-4 test"}, ctx)
    assert create_result.status == "blocked", (
        f"cron_create must be blocked when executor is disabled, got {create_result.status!r}"
    )
    assert "cron_not_configured" in (create_result.error_code or "")

    # cron_list is a read-only introspection; it never blocks regardless of
    # scheduler attachment -- it returns ok with schedulerAttached=False.
    list_result = cron_list({}, ctx)
    assert list_result.status == "ok", (
        f"cron_list should always return ok (read-only), got {list_result.status!r}"
    )
    # Verify schedulerAttached is False so the model knows the scheduler is not live.
    items_payload = getattr(list_result, "output", None) or {}
    if isinstance(items_payload, dict):
        assert items_payload.get("schedulerAttached") is False


# ---------------------------------------------------------------------------
# build_default_watchers auto-attaches when scheduler executor is enabled
# ---------------------------------------------------------------------------

def test_build_default_watchers_attaches_when_executor_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """When MAGI_SCHEDULER_EXECUTOR_ENABLED=1, build_default_watchers() must
    set MAGI_SCHEDULER_ATTACHED so cron tools are unblocked after wiring."""
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)
    monkeypatch.setenv("MAGI_SCHEDULER_DB_PATH", str(tmp_path / "jobs.db"))

    # Suppress all channel/network wiring (no tokens in test env).
    monkeypatch.delenv("MAGI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_SLACK_BOT_TOKEN", raising=False)

    from magi_agent.gateway.watchers import build_default_watchers

    build_default_watchers()

    assert os.environ.get("MAGI_SCHEDULER_ATTACHED") in {"1", "true", "True"}


def test_build_default_watchers_does_not_attach_when_executor_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """With MAGI_SCHEDULER_EXECUTOR_ENABLED unset, attachment must NOT happen."""
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)
    monkeypatch.setenv("MAGI_SCHEDULER_DB_PATH", str(tmp_path / "jobs.db"))

    monkeypatch.delenv("MAGI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAGI_SLACK_BOT_TOKEN", raising=False)

    from magi_agent.gateway.watchers import build_default_watchers

    build_default_watchers()

    assert not os.environ.get("MAGI_SCHEDULER_ATTACHED")
