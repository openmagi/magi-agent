"""Guards the internal developer overview and what-works-today against
reverse-stale and undersell drift (PR 17-PR6).

Two factual doc gaps motivated these checks:

E8 — `internal/docs/developer-overview.md` was reverse-stale: it claimed the
ADK invocation was DISABLED / "no live invocation" even though the ADK runner
is live today (`magi_agent/adk_bridge/local_runner.py` constructs a real
`Runner(...)`), and it enumerated runtime modules that have since been deleted
on `origin/main` (turn_controller.py, model_routing.py,
runner_session_boundary.py, projection_write_boundary.py).

E9 — `docs/what-works-today.md` undersold the enforcement story: it stated "no
boundary verdict blocks output or side effects" even though the pre-final
completion/evidence gate (`magi_agent/cli/engine.py`) is default-ON and blocks
coding-turn output with `pre_final_evidence_gate_blocked`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV_OVERVIEW = ROOT / "internal" / "docs" / "developer-overview.md"
WHAT_WORKS = ROOT / "docs" / "what-works-today.md"

# Modules deleted on origin/main — the developer overview must not enumerate
# them as if they exist in the runtime package.
DELETED_RUNTIME_MODULES = (
    "turn_controller.py",
    "model_routing.py",
    "runner_session_boundary.py",
    "projection_write_boundary.py",
)


def test_developer_overview_does_not_claim_adk_invocation_disabled() -> None:
    text = DEV_OVERVIEW.read_text(encoding="utf-8")
    assert "ADK invocation DISABLED" not in text
    assert "no live invocation" not in text


def test_developer_overview_drops_deleted_runtime_modules() -> None:
    text = DEV_OVERVIEW.read_text(encoding="utf-8")
    for module in DELETED_RUNTIME_MODULES:
        assert module not in text, module


def test_developer_overview_reflects_live_adk_invocation() -> None:
    text = DEV_OVERVIEW.read_text(encoding="utf-8").lower()
    # The corrected status must describe live invocation, not a disabled stub.
    assert "live" in text and "adk" in text


def test_developer_overview_marks_default_off_gates_link_planned() -> None:
    text = DEV_OVERVIEW.read_text(encoding="utf-8")
    # The /docs/default-off-gates page does not exist yet — the link must be
    # marked planned so a contributor is not sent to a dead page.
    if "/docs/default-off-gates" in text:
        assert "planned" in text.lower()


def test_what_works_today_acknowledges_pre_final_gate_blocks() -> None:
    text = WHAT_WORKS.read_text(encoding="utf-8")
    # The undersell line must be corrected: the pre-final completion gate blocks.
    assert "no boundary verdict blocks output or side effects" not in text
    assert "pre_final_evidence_gate_blocked" in text or "pre-final" in text.lower()
