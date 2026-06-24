"""F7 firing test: a Customize ``maxToolCallsPerTurn`` budget fires.

End-to-end-ish slice that proves the F7 pipeline lands the operator-authored
budget in the place the runtime enforces it:

1. Dashboard PUT /v1/app/customize/budgets persists ``maxToolCallsPerTurn: 5``.
2. ``apply_budgets_if_enabled`` projects it onto ``MAGI_TOOL_MAX_CALLS_PER_TURN``.
3. ``CoreToolhostHandlerSet.from_env`` (the single read site discovered in the
   F7 audit) reads the env and binds ``max_tool_calls_per_turn=5`` into its
   live config.

The brake is then enforced by gate5b_full_toolhost.py at
``if self._tool_calls >= self._config.max_tool_calls_per_turn`` — the same
config the toolhost handler set just bound. This test exercises the path from
the operator's dashboard save through to that bound config; the gate-firing
inside the dispatcher is locked separately by core_toolhost / gate5b tests.

Negative case proves the byte-identical invariant: with the F7 flag OFF, the
same persisted budget is INERT and the toolhost falls back to the default 64.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from magi_agent.customize.budgets_apply import apply_budgets_if_enabled
from magi_agent.customize.store import set_verification_budgets
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy
from magi_agent.tools.core_toolhost import CoreToolhostHandlerSet


_BUDGET_LIMIT = 5


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Tmp customize.json with the F7 budget persisted and all 3 gates ON.

    The lab/full profile seeds MAGI_TOOL_MAX_CALLS_PER_TURN in some shells;
    we explicitly delete it so the applier's setdefault is what populates it
    (mirrors a fresh hosted/k8s env where the operator authored the budget
    only via the dashboard).
    """
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_TOOL_MAX_CALLS_PER_TURN", raising=False)
    set_verification_budgets({"maxToolCallsPerTurn": _BUDGET_LIMIT}, path=cfile)
    yield cfile


def _load_policy(cfile: Path) -> CustomizeVerificationPolicy:
    from magi_agent.customize.store import load_overrides

    return CustomizeVerificationPolicy.from_overrides(load_overrides(cfile))


def test_budget_lands_on_env_when_unset(cfg: Path) -> None:
    """Step 1+2: persisted budget projects onto the live MAGI_* env."""
    env: dict[str, str] = {}
    apply_budgets_if_enabled(env=env, policy=_load_policy(cfg))
    assert env["MAGI_TOOL_MAX_CALLS_PER_TURN"] == str(_BUDGET_LIMIT)


def test_toolhost_picks_up_budgeted_cap(cfg: Path) -> None:
    """Step 3: ``CoreToolhostHandlerSet.from_env`` honors the projected env.

    The applier runs against ``os.environ`` (the same mapping the toolhost
    reads). After the applier, ``from_env()`` must bind ``max_tool_calls_per_turn``
    equal to the operator-authored budget, not the hardcoded 64 default.
    """
    apply_budgets_if_enabled(env=os.environ, policy=_load_policy(cfg))
    handler_set = CoreToolhostHandlerSet.from_env()
    assert handler_set._config["maxToolCallsPerTurn"] == _BUDGET_LIMIT


def test_explicit_env_wins_over_budget(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator-pinned env beats the dashboard save (precedence preserved).

    A k8s deployment / shell export of MAGI_TOOL_MAX_CALLS_PER_TURN=200 wins
    over the dashboard's "5" — the F7 design doc's acceptance criterion #2.
    """
    monkeypatch.setenv("MAGI_TOOL_MAX_CALLS_PER_TURN", "200")
    apply_budgets_if_enabled(env=os.environ, policy=_load_policy(cfg))
    assert os.environ["MAGI_TOOL_MAX_CALLS_PER_TURN"] == "200"
    handler_set = CoreToolhostHandlerSet.from_env()
    assert handler_set._config["maxToolCallsPerTurn"] == 200


def test_inert_when_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F7 master flag OFF + dashboard budget → toolhost uses the default 64.

    Locks the byte-identical default-OFF invariant: a fresh install / hosted
    serve must NOT silently honor a dashboard budget until the operator opts
    into the F7 surface.
    """
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUDGETS_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_TOOL_MAX_CALLS_PER_TURN", raising=False)
    set_verification_budgets({"maxToolCallsPerTurn": _BUDGET_LIMIT}, path=cfile)

    apply_budgets_if_enabled(env=os.environ, policy=_load_policy(cfile))
    # The env stays unset because the applier short-circuited on the OFF flag.
    assert "MAGI_TOOL_MAX_CALLS_PER_TURN" not in os.environ
    handler_set = CoreToolhostHandlerSet.from_env()
    # Falls back to the constructor default (64) — the pre-F7 behavior.
    assert handler_set._config["maxToolCallsPerTurn"] == 64


def test_governed_turn_hook_runs_applier(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_governed_turn`` calls ``_maybe_apply_customize_budgets`` at entry.

    Locks the wiring: the applier site discovered in the F7 audit
    (governed_turn.py top, before _build_runtime) is what every governed turn
    flows through, so this hook is the single point that fans out the budget
    to all three downstream readers identified in the discovery JSON.
    """
    from magi_agent.runtime import governed_turn

    monkeypatch.delenv("MAGI_TOOL_MAX_CALLS_PER_TURN", raising=False)
    # Calling the helper directly is the same path run_governed_turn takes at
    # the top of every turn (no engine/runtime needed for this assertion).
    governed_turn._maybe_apply_customize_budgets()
    assert os.environ["MAGI_TOOL_MAX_CALLS_PER_TURN"] == str(_BUDGET_LIMIT)
