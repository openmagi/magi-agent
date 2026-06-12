from __future__ import annotations

import pytest

from magi_agent.plugins.native import missions
from magi_agent.tools.context import ToolContext

_HONEST_FLAG = "MAGI_NATIVE_RECEIPTS_HONEST"
_MISSION_LEDGER_ATTACHED_FLAG = "MAGI_MISSION_LEDGER_ATTACHED"


def _context() -> ToolContext:
    return ToolContext(bot_id="bot-test", session_id="session-1")


@pytest.fixture(autouse=True)
def _isolate_backing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Honest receipts default ON; mission backing (cluster 03/01) inert by default.
    monkeypatch.delenv(_HONEST_FLAG, raising=False)
    monkeypatch.delenv(_MISSION_LEDGER_ATTACHED_FLAG, raising=False)


# ---------------------------------------------------------------------------
# honest-by-default: MissionLedger -> blocked mission_ledger_not_configured
# ---------------------------------------------------------------------------


def test_mission_ledger_is_honest_not_configured_by_default() -> None:
    result = missions.mission_ledger({"objective": "ship the thing"}, _context())

    assert result.status == "blocked"
    assert result.error_code == "mission_ledger_not_configured"
    # The model must not receive a success digest it can mis-report as "registered".
    assert result.output is None


# ---------------------------------------------------------------------------
# rollback safety: legacy fake-ok preserved when flag disabled
# ---------------------------------------------------------------------------


def test_legacy_fake_ok_preserved_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_HONEST_FLAG, "0")

    result = missions.mission_ledger({"objective": "ship the thing"}, _context())

    assert result.status == "ok"
    assert result.output is not None
    record = result.output["record"]
    assert record["status"] == "local_recorded"
    assert record["objective"] == "ship the thing"


# ---------------------------------------------------------------------------
# live-seam: mission backing attached -> delegate (not blocked)
# ---------------------------------------------------------------------------


def test_mission_ledger_delegates_when_backing_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_MISSION_LEDGER_ATTACHED_FLAG, "1")

    result = missions.mission_ledger({"objective": "ship the thing"}, _context())

    # backing-attached path must not emit the not_configured honest error.
    assert result.error_code != "mission_ledger_not_configured"
