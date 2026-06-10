from __future__ import annotations

import pytest

from magi_agent.plugins.native import skills
from magi_agent.tools.context import ToolContext

_HONEST_FLAG = "MAGI_NATIVE_RECEIPTS_HONEST"
_HOOKS_ATTACHED_FLAG = "MAGI_SKILL_RUNTIME_HOOKS_ATTACHED"


def _context() -> ToolContext:
    return ToolContext(bot_id="bot-test", session_id="session-1", turn_id="turn-1")


@pytest.fixture(autouse=True)
def _isolate_backing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Honest receipts default ON; the hook bus (cluster 11) is inert by default.
    monkeypatch.delenv(_HONEST_FLAG, raising=False)
    monkeypatch.delenv(_HOOKS_ATTACHED_FLAG, raising=False)


# ---------------------------------------------------------------------------
# honest-by-default: SkillRuntimeHooks -> blocked *_not_attached
# ---------------------------------------------------------------------------


def test_skill_runtime_hooks_is_honest_not_attached_by_default() -> None:
    result = skills.skill_runtime_hooks({}, _context())

    assert result.status == "blocked"
    assert result.error_code == "skill_runtime_hooks_not_attached"
    # The model must not receive a fixed hook tuple it can mis-report as
    # "hooks are wired and running".
    assert result.output is None


# ---------------------------------------------------------------------------
# rollback safety: legacy fake-ok preserved when flag disabled
# ---------------------------------------------------------------------------


def test_legacy_fake_ok_preserved_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_HONEST_FLAG, "0")

    result = skills.skill_runtime_hooks({}, _context())

    assert result.status == "ok"
    assert result.output is not None
    assert "hooks" in result.output
    assert "hookDigest" in result.output


# ---------------------------------------------------------------------------
# live-seam: hook bus attached -> delegate (not the honest not_attached error)
# ---------------------------------------------------------------------------


def test_skill_runtime_hooks_delegates_when_hook_bus_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_HOOKS_ATTACHED_FLAG, "1")

    result = skills.skill_runtime_hooks({}, _context())

    assert result.error_code != "skill_runtime_hooks_not_attached"


# ---------------------------------------------------------------------------
# regression: SkillLoader is genuinely real and must stay untouched
# ---------------------------------------------------------------------------


def test_skill_loader_still_loads_real_skills() -> None:
    result = skills.skill_loader({}, _context())

    assert result.status == "ok"
    assert result.output is not None
    assert "skills" in result.output
    assert "loadedSkills" in result.output


# ---------------------------------------------------------------------------
# regression: ExternalToolLoader is already honest -> must stay honest
# ---------------------------------------------------------------------------


def test_external_tool_loader_stays_honest_metadata_only() -> None:
    result = skills.external_tool_loader({}, _context())

    assert result.status == "ok"
    assert result.output is not None
    assert result.output["executionAttached"] is False
