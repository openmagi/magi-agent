"""U3 -- scheduler attachment seam.

TDD: when MAGI_SCHEDULER_EXECUTOR_ENABLED is set, build_default_watchers() /
build_local_scheduler_cron_driver() sets MAGI_SCHEDULER_ATTACHED in the process
environment so native cron tools route past the honest-block to the live store.

Without the attachment seam the cron tools always return cron_not_configured;
with it they accept CronCreate calls.
"""
from __future__ import annotations

import os
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Attachment seam: attach_local_scheduler sets MAGI_SCHEDULER_ATTACHED
# ---------------------------------------------------------------------------

def test_attach_local_scheduler_sets_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)

    from magi_agent.gateway.watchers import attach_local_scheduler

    attach_local_scheduler()
    assert os.environ.get("MAGI_SCHEDULER_ATTACHED") in {"1", "true", "True"}


def test_attach_local_scheduler_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling attach twice must not raise and env stays set."""
    monkeypatch.delenv("MAGI_SCHEDULER_ATTACHED", raising=False)

    from magi_agent.gateway.watchers import attach_local_scheduler

    attach_local_scheduler()
    attach_local_scheduler()
    assert os.environ.get("MAGI_SCHEDULER_ATTACHED")


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
