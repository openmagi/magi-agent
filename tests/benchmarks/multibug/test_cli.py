from __future__ import annotations

import pytest

from magi_agent.benchmarks.multibug.cli import (
    GateDisabledError,
    ensure_enabled,
    is_enabled,
)


def test_ensure_enabled_raises_when_unset() -> None:
    with pytest.raises(GateDisabledError):
        ensure_enabled(env={})


def test_ensure_enabled_passes_when_truthy() -> None:
    # Should not raise.
    ensure_enabled(env={"MAGI_MULTIBUG_HARNESS_ENABLED": "1"})


def test_is_enabled_truthy_values() -> None:
    assert is_enabled(env={"MAGI_MULTIBUG_HARNESS_ENABLED": "yes"})
    assert is_enabled(env={"MAGI_MULTIBUG_HARNESS_ENABLED": "true"})
    assert not is_enabled(env={"MAGI_MULTIBUG_HARNESS_ENABLED": "0"})
    assert not is_enabled(env={})


def test_run_eval_gates_before_loading(tmp_path) -> None:
    # Even with a missing file, the gate must fire FIRST (no FileNotFoundError).
    with pytest.raises(GateDisabledError):
        from magi_agent.benchmarks.multibug.cli import run_eval

        run_eval(
            str(tmp_path / "nope.jsonl"),
            output_dir=str(tmp_path / "out"),
            env={},
        )
