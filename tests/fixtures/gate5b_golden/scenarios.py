"""Two scenario drivers over the REAL Gate5BFullToolHost.

``dispatch_ok``      — the 8 deterministic legacy tools succeed end-to-end.
``dispatch_blocked`` — one block per policy family: allowlist, path policy,
memory-mode (incognito), read-ledger (edit without fresh read), call budget.
NOTE the budget family needs one interleaved ok call: ``Gate5BFullToolCounter``
increments ``_tool_calls`` only in ``finish_call`` (blocked outcomes are NEVER
budget-counted), so ``max_tool_calls_exhausted`` only fires after a completion.

Construction copied from tests/gates/test_file_tool_path_alias.py; memory-mode
args copied from tests/gates/test_gate5b_full_toolhost_memory_mode.py;
read-ledger trigger copied from tests/gates/test_gate5b_read_ledger.py.
Workspace = a fresh tmp dir per run; every recorded field is content-addressed
relative paths/outputs, so the trace is machine-independent.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from tests.fixtures.gate5b_golden.recorder import Gate5BDispatchRecorder, normalize_trace

_FIXED_NOW_MS = 1_700_000_000_000


def _pin_env() -> dict[str, str | None]:
    """Pin call-time env flags the handlers read so the trace is stable.
    Returns the previous values for restoration."""
    pinned = {"MAGI_EDIT_FUZZY_MATCH_ENABLED": "0"}
    previous: dict[str, str | None] = {}
    for key, value in pinned.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _host(workspace: Path, **overrides: Any):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    kwargs: dict[str, Any] = dict(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "environment": "local",
                "environmentAllowlist": ["local"],
                "maxToolCallsPerTurn": overrides.pop("max_calls", 32),
                "maxPerToolOutputBytes": 8192,
                "commandTimeoutMs": 5000,
            }
        ),
        workspace_root=workspace,
        exposed_tool_names=overrides.pop(
            "exposed",
            ("Clock", "Calculation", "FileRead", "FileWrite", "FileEdit",
             "Glob", "Grep", "GitDiff"),
        ),
        now_ms=lambda: _FIXED_NOW_MS,
    )
    kwargs.update(overrides)
    return Gate5BFullToolHost(**kwargs)


async def _drive(host: Any, rec: Gate5BDispatchRecorder,
                 calls: list[tuple[str, dict[str, Any]]]) -> None:
    for index, (tool, args) in enumerate(calls):
        outcome = await host.dispatch(
            tool,
            args,
            request_digest=f"req-{index}",
            tool_call_id=f"call-{index}",
        )
        rec.record_dispatch(tool_name=tool, args=dict(args), outcome=outcome)


def _ok_calls() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("Clock", {}),
        ("Calculation", {"expression": "6*7"}),
        ("FileWrite", {"path": "notes/a.txt", "content": "hello golden\n"}),
        ("FileRead", {"path": "notes/a.txt"}),
        ("FileEdit", {"path": "notes/a.txt",
                      "oldText": "hello", "newText": "hi"}),
        ("Glob", {"pattern": "**/*.txt"}),
        ("Grep", {"pattern": "hi", "glob": "**/*.txt"}),
        ("GitDiff", {}),
    ]


def run_dispatch_ok_scenario(**host_overrides: Any) -> list[dict[str, Any]]:
    """8-tool happy path. ``host_overrides`` lets the C1 equivalence tests run
    the IDENTICAL trace through a pack-loaded host (handlers/policies injected)
    — byte-equality against the committed golden is the migration proof."""
    previous = _pin_env()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            host = _host(workspace, **host_overrides)
            rec = Gate5BDispatchRecorder()
            asyncio.run(_drive(host, rec, _ok_calls()))
            return normalize_trace(rec.events)
    finally:
        _restore_env(previous)


def _blocked_calls() -> list[tuple[str, dict[str, Any]]]:
    return [
        # 1. not allowlisted (blocked outcomes never consume budget)
        ("Bash", {"command": "printf hi"}),
        # 2. workspace escape -> path_policy_denied
        ("FileRead", {"path": "../escape.txt"}),
        # 3. incognito read of protected memory -> memory_mode_blocked
        ("FileRead", {"path": "memory/MEMORY.md"}),
        # 4. edit of existing file without a fresh full read
        #    -> read_ledger_* block (record=False, not budget-counted)
        ("FileEdit", {"path": "existing.txt",
                      "oldText": "already", "newText": "still"}),
        # 5. the ONLY ok completion -> finish_call consumes the
        #    whole maxToolCallsPerTurn=1 budget
        ("Clock", {}),
        # 6. budget: second completion attempt (fresh call id)
        #    -> max_tool_calls_exhausted at before_call
        ("Clock", {}),
    ]


def run_dispatch_blocked_scenario(**host_overrides: Any) -> list[dict[str, Any]]:
    """One block per policy family (5 distinct reasons + 1 ok budget arm)."""
    previous = _pin_env()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            # Pre-existing file so the read-ledger edit check has a target.
            (workspace / "existing.txt").write_text("already here\n", encoding="utf-8")
            # Protected-memory file (path shape verified against
            # magi_agent/tools/memory_mode_guard.is_protected_memory_path:
            # "memory/" prefix is protected).
            (workspace / "memory").mkdir()
            (workspace / "memory" / "MEMORY.md").write_text("secret\n", encoding="utf-8")
            overrides: dict[str, Any] = dict(
                exposed=("Clock", "FileRead", "FileEdit"),
                read_ledger_enabled=True,
                memory_mode="incognito",
                max_calls=1,
            )
            overrides.update(host_overrides)
            host = _host(workspace, **overrides)
            rec = Gate5BDispatchRecorder()
            asyncio.run(_drive(host, rec, _blocked_calls()))
            return normalize_trace(rec.events)
    finally:
        _restore_env(previous)
