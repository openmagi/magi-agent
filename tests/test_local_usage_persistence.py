"""End-to-end: a local serve turn's usage is persisted and read back by the
``/v1/app/runtime`` dashboard reader.

This guards the contract the Usage page depends on — the writer
(``streaming_chat_route._persist_local_turn_usage``) and the reader
(``app_api._session_items`` / ``_runtime_snapshot``) must agree on the same
workspace SQLite DB, table, and identity keys.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from magi_agent.transport import app_api
from magi_agent.transport.streaming_chat_route import _persist_local_turn_usage


@dataclass
class _Config:
    model: str = "claude-sonnet-4-5"
    user_id: str = "local-user"


class _ToolRegistry:
    def list_all(self) -> list:
        return []


class _Runtime:
    def __init__(self) -> None:
        self.config = _Config()
        self.tool_registry = _ToolRegistry()


class _Terminal:
    def __init__(self, usage: dict) -> None:
        self.usage = usage


def test_local_turn_usage_persists_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    runtime = _Runtime()

    _persist_local_turn_usage(
        runtime,
        "sess-1",
        _Terminal({"input_tokens": 120, "output_tokens": 40, "cache_read_tokens": 5}),
    )
    # A second turn on the same session accumulates.
    _persist_local_turn_usage(
        runtime,
        "sess-1",
        _Terminal({"input_tokens": 30, "output_tokens": 10}),
    )

    items = app_api._session_items(runtime)
    assert len(items) == 1
    budget = items[0]["budget"]
    assert budget["turns"] == 2
    assert budget["inputTokens"] == 150
    assert budget["outputTokens"] == 50
    assert isinstance(budget["costUsd"], float)

    snapshot = app_api._runtime_snapshot(runtime)
    assert snapshot["sessions"]["count"] == 1


def test_zero_token_turn_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    runtime = _Runtime()

    _persist_local_turn_usage(runtime, "sess-empty", _Terminal({}))
    _persist_local_turn_usage(
        runtime, "sess-empty", _Terminal({"input_tokens": 0, "output_tokens": 0})
    )

    assert app_api._session_items(runtime) == []


def test_price_override_yields_nonzero_cost_for_unmapped_model(tmp_path, monkeypatch):
    # A model litellm cannot price still gets a real cost when the operator
    # declares per-MTok rates via env.
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MAGI_USAGE_PRICE_IN_PER_MTOK", "0.6")
    monkeypatch.setenv("MAGI_USAGE_PRICE_OUT_PER_MTOK", "2.5")

    runtime = _Runtime()
    runtime.config = _Config(model="kimi-k2p6")  # litellm has no price for this id

    _persist_local_turn_usage(
        runtime,
        "sess-kimi",
        _Terminal({"input_tokens": 1_000_000, "output_tokens": 1_000_000}),
    )

    items = app_api._session_items(runtime)
    assert len(items) == 1
    assert items[0]["budget"]["costUsd"] == pytest.approx(0.6 + 2.5)
