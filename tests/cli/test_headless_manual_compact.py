"""G7: headless /compact surface wiring (manual force signal).

Drives the REAL ``_dispatch_headless_command`` through a registry with the real
``CompactCommand`` so the ``Compact()`` branch is exercised end-to-end.
"""

from __future__ import annotations

import asyncio

import pytest

from magi_agent.cli.commands.builtins import BUILTIN_BOTH, CompactCommand
from magi_agent.cli.commands.registry import CommandRegistryImpl
from magi_agent.cli.headless import _dispatch_headless_command
from magi_agent.runtime.manual_compaction_context import (
    MAGI_COMPACTION_MANUAL_ENABLED_ENV,
    consume_manual_compaction,
    reset_manual_compaction,
)


@pytest.fixture(autouse=True)
def _isolate_signal():
    reset_manual_compaction()
    yield
    reset_manual_compaction()


def _registry() -> CommandRegistryImpl:
    reg = CommandRegistryImpl()
    reg.register(CompactCommand(name="compact", surface=BUILTIN_BOTH))
    return reg


def _run(coro):
    return asyncio.run(coro)


def test_flag_off_returns_stub_and_sets_no_signal(monkeypatch) -> None:
    monkeypatch.delenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, raising=False)
    kind, blocks, message = _run(
        _dispatch_headless_command("/compact", commands=_registry(), cwd=".")
    )
    assert kind == "local"
    assert blocks is None
    assert message == "[compact] context compaction requested"
    # No signal set.
    assert consume_manual_compaction() is False


def test_flag_on_sets_signal_and_honest_message(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, "1")
    kind, blocks, message = _run(
        _dispatch_headless_command("/compact", commands=_registry(), cwd=".")
    )
    assert kind == "local"
    assert blocks is None
    assert message == "[compact] context compaction will run on the next message"
    # Signal was set (consumed exactly once).
    assert consume_manual_compaction() is True
    assert consume_manual_compaction() is False
