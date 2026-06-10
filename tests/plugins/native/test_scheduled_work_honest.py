from __future__ import annotations

import pytest

from magi_agent.plugins.native import scheduled_work
from magi_agent.tools.context import ToolContext

_HONEST_FLAG = "MAGI_NATIVE_RECEIPTS_HONEST"
_SCHEDULER_ATTACHED_FLAG = "MAGI_SCHEDULER_ATTACHED"
_BACKGROUND_TASKS_ATTACHED_FLAG = "MAGI_BACKGROUND_TASKS_ATTACHED"


def _context() -> ToolContext:
    return ToolContext(bot_id="bot-test", session_id="session-1")


@pytest.fixture(autouse=True)
def _isolate_backing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Honest receipts default ON; backing systems (cluster 03) inert by default.
    monkeypatch.delenv(_HONEST_FLAG, raising=False)
    monkeypatch.delenv(_SCHEDULER_ATTACHED_FLAG, raising=False)
    monkeypatch.delenv(_BACKGROUND_TASKS_ATTACHED_FLAG, raising=False)


# ---------------------------------------------------------------------------
# honest-by-default: cron mutating ops -> blocked cron_not_configured
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        scheduled_work.cron_create,
        scheduled_work.cron_update,
        scheduled_work.cron_delete,
    ],
)
def test_cron_mutating_handlers_are_honest_not_configured_by_default(handler) -> None:
    result = handler({"schedule": "0 9 * * *", "task": "daily report"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "cron_not_configured"
    # The model must not receive a success digest it can mis-report as "scheduled".
    assert result.output is None


def test_cron_list_is_honest_empty_when_scheduler_unattached() -> None:
    result = scheduled_work.cron_list({}, _context())

    assert result.status == "ok"
    assert result.output is not None
    assert result.output["items"] == ()
    assert result.output["schedulerAttached"] is False


# ---------------------------------------------------------------------------
# honest-by-default: background-task ops -> blocked background_tasks_not_configured
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        scheduled_work.task_wait,
        scheduled_work.task_get,
        scheduled_work.task_output,
        scheduled_work.task_stop,
    ],
)
def test_task_handlers_are_honest_not_configured_by_default(handler) -> None:
    result = handler({"taskId": "task-a"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "background_tasks_not_configured"
    assert result.output is None


def test_task_list_is_honest_empty_when_runtime_unattached() -> None:
    result = scheduled_work.task_list({}, _context())

    assert result.status == "ok"
    assert result.output is not None
    assert result.output["items"] == ()
    assert result.output["backgroundTasksAttached"] is False


# ---------------------------------------------------------------------------
# rollback safety: legacy fake-ok preserved when flag disabled
# ---------------------------------------------------------------------------


def test_legacy_fake_ok_preserved_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_HONEST_FLAG, "0")

    cron = scheduled_work.cron_create({"schedule": "0 9 * * *"}, _context())
    task = scheduled_work.task_wait({"taskId": "task-a"}, _context())

    assert cron.status == "ok"
    assert cron.output is not None
    assert cron.output["toolName"] == "CronCreate"
    assert task.status == "ok"
    assert task.output is not None
    assert task.output["toolName"] == "TaskWait"


# ---------------------------------------------------------------------------
# live-seam: scheduler/background backing attached -> delegate (not blocked)
# ---------------------------------------------------------------------------


def test_cron_create_delegates_when_scheduler_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_SCHEDULER_ATTACHED_FLAG, "1")

    result = scheduled_work.cron_create({"schedule": "0 9 * * *"}, _context())

    # backing-attached path must not emit the not_configured honest error.
    assert result.error_code != "cron_not_configured"


def test_task_wait_delegates_when_background_tasks_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_BACKGROUND_TASKS_ATTACHED_FLAG, "1")

    result = scheduled_work.task_wait({"taskId": "task-a"}, _context())

    assert result.error_code != "background_tasks_not_configured"
