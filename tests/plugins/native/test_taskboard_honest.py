from __future__ import annotations

import pytest

from magi_agent.plugins.native import taskboard
from magi_agent.tools.context import ToolContext

_HONEST_FLAG = "MAGI_NATIVE_RECEIPTS_HONEST"
_NOTIFY_ATTACHED_FLAG = "MAGI_NOTIFY_CHANNEL_ATTACHED"
_MODE_SWITCH_ATTACHED_FLAG = "MAGI_MODE_SWITCH_ATTACHED"
_MEMORY_REDACT_ATTACHED_FLAG = "MAGI_MEMORY_REDACT_ATTACHED"


def _context() -> ToolContext:
    return ToolContext(bot_id="bot-test", session_id="session-1", turn_id="turn-1")


@pytest.fixture(autouse=True)
def _isolate_backing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Honest receipts default ON; backing systems (cluster 14/01/B17) inert by default.
    monkeypatch.delenv(_HONEST_FLAG, raising=False)
    monkeypatch.delenv(_NOTIFY_ATTACHED_FLAG, raising=False)
    monkeypatch.delenv(_MODE_SWITCH_ATTACHED_FLAG, raising=False)
    monkeypatch.delenv(_MEMORY_REDACT_ATTACHED_FLAG, raising=False)


# ---------------------------------------------------------------------------
# honest-by-default: fake mission handlers -> blocked *_not_configured
# ---------------------------------------------------------------------------


def test_notify_user_is_honest_not_configured_by_default() -> None:
    result = taskboard.notify_user({"message": "done"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "notify_user_not_configured"
    # The model must not receive a success digest it can mis-report as "notified".
    assert result.output is None


def test_switch_to_act_mode_is_honest_unsupported_by_default() -> None:
    result = taskboard.switch_to_act_mode({}, _context())

    assert result.status == "blocked"
    assert result.error_code == "mode_switch_unsupported_local"
    # SwitchToActMode must not pretend the turn/permission state changed.
    assert result.output is None


def test_memory_redact_is_honest_not_attached_by_default() -> None:
    result = taskboard.memory_redact({"target": "mem-1"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "memory_redaction_not_attached"
    assert result.output is None


# ---------------------------------------------------------------------------
# task_board stays real (already persists) — must not be turned into a stub
# ---------------------------------------------------------------------------


def test_task_board_still_persists_when_honest(tmp_path) -> None:
    context = ToolContext(
        bot_id="bot-test",
        session_id="session-1",
        workspace_root=str(tmp_path),
    )

    added = taskboard.task_board({"action": "add", "title": "alpha"}, context)
    listed = taskboard.task_board({"action": "list"}, context)

    assert added.status == "ok"
    assert listed.status == "ok"
    assert listed.output is not None
    assert listed.output["taskCount"] == 1


# ---------------------------------------------------------------------------
# rollback safety: legacy fake-ok preserved when flag disabled
# ---------------------------------------------------------------------------


def test_legacy_fake_ok_preserved_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_HONEST_FLAG, "0")

    notify = taskboard.notify_user({"message": "done"}, _context())
    mode = taskboard.switch_to_act_mode({}, _context())
    redact = taskboard.memory_redact({"target": "mem-1"}, _context())

    assert notify.status == "ok"
    assert notify.output is not None
    assert "messageDigest" in notify.output
    assert mode.status == "ok"
    assert mode.output is not None
    assert mode.output["requestedMode"] == "act"
    assert redact.status == "ok"
    assert redact.output is not None
    assert redact.output["redactionRecorded"] is True


# ---------------------------------------------------------------------------
# live-seam: backing attached -> delegate (not the honest not_configured error)
# ---------------------------------------------------------------------------


def test_notify_user_delegates_when_channel_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_NOTIFY_ATTACHED_FLAG, "1")

    result = taskboard.notify_user({"message": "done"}, _context())

    assert result.error_code != "notify_user_not_configured"


def test_switch_to_act_mode_delegates_when_mode_switch_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_MODE_SWITCH_ATTACHED_FLAG, "1")

    result = taskboard.switch_to_act_mode({}, _context())

    assert result.error_code != "mode_switch_unsupported_local"


def test_memory_redact_delegates_when_redact_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_MEMORY_REDACT_ATTACHED_FLAG, "1")

    result = taskboard.memory_redact({"target": "mem-1"}, _context())

    assert result.error_code != "memory_redaction_not_attached"
