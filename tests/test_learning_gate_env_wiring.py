"""Task 5 — executor honors explicit ``eval_gate_config`` else reads env.

The live entry point ``harness/learning_executor.run_reflection`` resolves the
eval-gate config as ``eval_gate_config if eval_gate_config is not None else
eval_gate_config_from_env()`` and passes the result to ``run_eval_gate``.

Hard invariant (no default flip): with NO ``MAGI_LEARNING_GATE_*`` env set and
no explicit config, the resolved config is strict_band — byte-identical to
today.  Operator opt-in to ``paired_significance`` is via env ONLY.

We monkeypatch ``run_eval_gate`` (in the executor's module namespace) to capture
the config it receives, then drive a minimal ON+store reflection pass.  All
``MAGI_LEARNING_GATE_*`` vars are cleared so the real environment can't leak in.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.harness import learning_executor
from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    _REFLECTION_ENV_VAR,
    run_reflection,
)
from magi_agent.learning.candidates import LocalFakeTranscriptSource, SessionTrace
from magi_agent.learning.eval_gate import EvalGateConfig
from magi_agent.learning.store import SqliteLearningStore


_GATE_ENV_VARS = (
    "MAGI_LEARNING_GATE_RULE",
    "MAGI_LEARNING_GATE_Z",
    "MAGI_LEARNING_GATE_N_REPEATS",
    "MAGI_LEARNING_GATE_MAX_REPEATS",
)


def _clear_gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GATE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _make_trace(session_id: str = "s1") -> SessionTrace:
    return SessionTrace(
        session_id=session_id,
        turns=(
            {"role": "user", "text": "hello"},
            {"role": "agent", "text": "done"},
        ),
        final_output="done",
        ts="2026-06-03T10:00:00Z",
    )


def _capture_gate_config(
    monkeypatch: pytest.MonkeyPatch,
) -> list[EvalGateConfig | None]:
    """Patch ``run_eval_gate`` in the executor module; record the config arg."""
    captured: list[EvalGateConfig | None] = []

    def _fake_run_eval_gate(candidates, *, config=None, **kwargs):  # noqa: ANN001
        captured.append(config)
        return ()

    monkeypatch.setattr(learning_executor, "run_eval_gate", _fake_run_eval_gate)
    return captured


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path, *, eval_gate_config=None):
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    store = SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))
    source = LocalFakeTranscriptSource(traces=(_make_trace(),))
    return asyncio.run(
        run_reflection(
            source=source,
            config=LearningReflectionConfig(enabled=True),
            store=store,
            eval_gate_config=eval_gate_config,
        )
    )


# ---------------------------------------------------------------------------
# Env unset → strict_band (NO default flip)
# ---------------------------------------------------------------------------


def test_env_unset_resolves_strict_band(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clear_gate_env(monkeypatch)
    captured = _capture_gate_config(monkeypatch)
    _run(monkeypatch, tmp_path)
    assert len(captured) == 1
    cfg = captured[0]
    assert cfg is not None
    assert cfg.decision_rule == "strict_band"
    # Byte-identical to today's no-config behavior.
    assert cfg == EvalGateConfig()


# ---------------------------------------------------------------------------
# Env selects paired_significance → executor resolves paired config
# ---------------------------------------------------------------------------


def test_env_selects_paired_significance(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clear_gate_env(monkeypatch)
    monkeypatch.setenv("MAGI_LEARNING_GATE_RULE", "paired_significance")
    captured = _capture_gate_config(monkeypatch)
    _run(monkeypatch, tmp_path)
    assert captured[0] is not None
    assert captured[0].decision_rule == "paired_significance"


# ---------------------------------------------------------------------------
# Explicit config wins over env
# ---------------------------------------------------------------------------


def test_explicit_config_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _clear_gate_env(monkeypatch)
    # Env says paired, but caller passes an explicit strict_band config.
    monkeypatch.setenv("MAGI_LEARNING_GATE_RULE", "paired_significance")
    captured = _capture_gate_config(monkeypatch)
    explicit = EvalGateConfig(decision_rule="strict_band")
    _run(monkeypatch, tmp_path, eval_gate_config=explicit)
    assert captured[0] is explicit
    assert captured[0].decision_rule == "strict_band"
