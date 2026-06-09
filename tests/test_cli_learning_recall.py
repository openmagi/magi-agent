"""TDD tests for CLI learning recall block injection.

Tests are written FIRST (red), then the implementation makes them green.

Contract:
  1. Gate off (default): build_cli_learning_recall_block returns "" even when
     the store has active items.
  2. Gate on + store has active items: returns a block containing the item text.
  3. Gate on + incognito memory_mode: returns "".
  4. Gate on + no db / no entries: returns "".
  5. Store error is non-fatal: returns "" (e.g. bogus workspace).
  6. build_cli_instruction default-off byte-identical: with injection off, the
     returned instruction does NOT contain the learning-block header. With
     injection on + seeded store, the instruction CONTAINS the learning block
     header.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BLOCK_HEADER = "## Learned from past sessions"

_ENV_INJECTION = "MAGI_LEARNING_INJECTION_ENABLED"
_ENV_MASTER = "MAGI_LEARNING_ENABLED"


def _make_store(tmp_path: Path):
    """Return a SqliteLearningStore backed by a temp directory.

    Uses DEFAULT_LEARNING_DB_PATH so the db path matches the one that
    build_cli_learning_recall_block will look up.
    """
    from magi_agent.learning.store import DEFAULT_LEARNING_DB_PATH, SqliteLearningStore

    return SqliteLearningStore(db_path=DEFAULT_LEARNING_DB_PATH, workspace_root=str(tmp_path))


def _seed_active_item(
    store,
    *,
    rationale: str = "prefer concise answers",
    task_kind: str = "general",
) -> None:
    """Land a genuine active item via the real eval-gate pipeline."""
    from magi_agent.learning.candidates import LearningCandidate
    from magi_agent.learning.eval_gate import StaticCheckSet, run_eval_gate
    from magi_agent.learning.models import LearningScope, Provenance

    candidate = LearningCandidate(
        kind="example",
        scope=LearningScope(taskKind=task_kind, tags=("style",)),
        content={"situation": "user asks", "behavior": rationale},
        rationale=rationale,
        provenance=Provenance(
            sessionIds=("sess-1",),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
        sourceSignalRef="signal:diff@sess-1",
    )
    checkset = StaticCheckSet(before=(1.0, 1.0, 1.0, 1.0), after=(1.0, 1.0, 1.0, 1.0))
    run_eval_gate((candidate,), store=store, checkset=checkset)


def _apply_local_full_defaults_to_process(
    monkeypatch: pytest.MonkeyPatch,
    *,
    explicit_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Apply local full defaults to os.environ through monkeypatch."""
    from magi_agent.runtime.local_defaults import (
        LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
        apply_local_full_runtime_defaults,
    )

    env = dict(explicit_env or {})
    apply_local_full_runtime_defaults(env)

    for key in set(LOCAL_FULL_RUNTIME_ENV_DEFAULTS) | {_ENV_INJECTION, _ENV_MASTER}:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


# ---------------------------------------------------------------------------
# 1. Gate off (default) — returns "" even with active items
# ---------------------------------------------------------------------------


def test_gate_off_returns_empty_even_with_active_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When injection gate is off (default), block is always ""."""
    # Ensure both master and injection env vars are not set so we get defaults.
    # Master defaults ON; injection defaults OFF — so injection_effective is False.
    monkeypatch.delenv(_ENV_INJECTION, raising=False)

    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="normal"
    )
    assert result == ""


# ---------------------------------------------------------------------------
# 2. Gate on + store has active items — returns formatted block
# ---------------------------------------------------------------------------


def test_gate_on_returns_block_with_active_item_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With gate on and a seeded active item, returns the formatted block."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="normal"
    )
    assert result != ""
    assert _BLOCK_HEADER in result
    assert "always write tests first" in result


# ---------------------------------------------------------------------------
# 3. Gate on + incognito memory_mode — returns ""
# ---------------------------------------------------------------------------


def test_gate_on_incognito_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Incognito memory mode suppresses the learning block even when gate is on."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="incognito"
    )
    assert result == ""


# ---------------------------------------------------------------------------
# 4. Gate on + no db / no entries — returns ""
# ---------------------------------------------------------------------------


def test_gate_on_no_db_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the learning db does not exist yet, returns ""."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    # tmp_path exists but no learning.db has been written
    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="normal"
    )
    assert result == ""


def test_gate_on_empty_store_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the store exists but has no active items, returns ""."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    # Create the store (so the db file exists) but don't seed any items
    _make_store(tmp_path)  # creates the db file
    # Force the db file to exist by calling a no-op read
    store = _make_store(tmp_path)
    store.list(tenant_id="local")  # initializes the schema

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="normal"
    )
    assert result == ""


# ---------------------------------------------------------------------------
# 5. workspace_root=None — returns ""
# ---------------------------------------------------------------------------


def test_none_workspace_root_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When workspace_root is None, returns "" regardless of gate."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(workspace_root=None, memory_mode="normal")
    assert result == ""


# ---------------------------------------------------------------------------
# 6. Store error is non-fatal — returns ""
# ---------------------------------------------------------------------------


def test_store_error_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Any error from the store returns "", never raises."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    # Point at a workspace that exists but whose .openmagi/learning.db path is
    # actually a directory (causes sqlite3 to fail on open).
    bogus_db_dir = tmp_path / ".openmagi" / "learning.db"
    bogus_db_dir.mkdir(parents=True, exist_ok=True)

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="normal"
    )
    assert result == ""


# ---------------------------------------------------------------------------
# New: read_only memory_mode still injects (only incognito suppresses)
# ---------------------------------------------------------------------------


def test_gate_on_read_only_still_injects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """read_only memory_mode does NOT suppress learning injection.

    Only incognito suppresses learning recall.  read_only means the agent
    cannot write new memories, but should still receive existing active
    learnings in the prompt.

    This test locks the behavior against a future regression where someone
    over-broadens the incognito guard to include read_only.
    """
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="prefer short variable names")

    from magi_agent.cli.learning_recall import build_cli_learning_recall_block

    result = build_cli_learning_recall_block(
        workspace_root=str(tmp_path), memory_mode="read_only"
    )
    assert result != "", "read_only should still inject learnings (only incognito suppresses)"
    assert _BLOCK_HEADER in result
    assert "prefer short variable names" in result


# ---------------------------------------------------------------------------
# 7. build_cli_instruction byte-identical when gate off
# ---------------------------------------------------------------------------


def test_build_cli_instruction_no_learning_block_when_gate_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With injection gate off, build_cli_instruction contains no learning header."""
    monkeypatch.delenv(_ENV_INJECTION, raising=False)
    # Also seed an active item to prove it's suppressed
    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="cli-test-gate-off",
        workspace_root=str(tmp_path),
    )
    assert _BLOCK_HEADER not in instruction


def test_build_cli_instruction_after_local_full_defaults_requires_explicit_injection_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Installed/local full defaults must not enable learning prompt injection."""
    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    _apply_local_full_defaults_to_process(monkeypatch)

    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="cli-test-local-full-defaults",
        workspace_root=str(tmp_path),
    )
    assert _BLOCK_HEADER not in instruction


def test_build_cli_instruction_after_local_full_defaults_injects_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit injection opt-in still works with installed/local full defaults."""
    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    _apply_local_full_defaults_to_process(
        monkeypatch,
        explicit_env={_ENV_INJECTION: "1"},
    )

    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="cli-test-local-full-defaults-opt-in",
        workspace_root=str(tmp_path),
    )
    assert _BLOCK_HEADER in instruction
    assert "always write tests first" in instruction


# ---------------------------------------------------------------------------
# 8. build_cli_instruction contains learning block when gate on
# ---------------------------------------------------------------------------


def test_build_cli_instruction_contains_learning_block_when_gate_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With injection gate on + seeded store, build_cli_instruction contains block."""
    monkeypatch.setenv(_ENV_INJECTION, "true")
    monkeypatch.setenv(_ENV_MASTER, "true")

    store = _make_store(tmp_path)
    _seed_active_item(store, rationale="always write tests first")

    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="cli-test-gate-on",
        workspace_root=str(tmp_path),
    )
    assert _BLOCK_HEADER in instruction
    assert "always write tests first" in instruction
