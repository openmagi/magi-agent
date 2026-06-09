# tests/benchmarks/taubench/test_cli.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.taubench.cli import GateDisabledError, ensure_enabled


def test_gate_blocks_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_TAUBENCH_ENABLED", raising=False)
    with pytest.raises(GateDisabledError):
        ensure_enabled()


def test_gate_allows_when_set(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TAUBENCH_ENABLED", "1")
    ensure_enabled()
