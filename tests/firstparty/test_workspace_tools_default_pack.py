"""C1 worked examples: bundled gate5b workspace tool handlers (no privilege).

Each migrated tool's pack handler must be BYTE-IDENTICAL to the legacy
``_handle`` branch: same outcome status and same receipt bounded-output digest
for the same inputs. Bases are passed explicitly (the bundled first-party root)
so the tests stay hermetic on machines that have ``~/.magi/packs``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _bundled_handlers():
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT])
    return registries.workspace_tool_handlers


def test_bundled_pack_registers_clock_workspace_handler():
    handlers = _bundled_handlers()
    assert callable(handlers.resolve("Clock"))
    assert callable(handlers.resolve("Calculation"))


def test_pack_clock_handler_is_byte_identical_to_legacy(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )

    def _outcome(host):
        return asyncio.run(
            host.dispatch("Clock", {}, request_digest="r", tool_call_id="c")
        )

    legacy = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path, exposed_tool_names=("Clock",),
        now_ms=lambda: 1_700_000_000_000,
    )
    handlers = _bundled_handlers()
    packed = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path, exposed_tool_names=("Clock",),
        now_ms=lambda: 1_700_000_000_000,
        workspace_handlers={"Clock": handlers.resolve("Clock")},
    )
    a, b = _outcome(legacy), _outcome(packed)
    assert a.status == b.status == "ok"
    assert a.receipt.bounded_output_digest == b.receipt.bounded_output_digest


def test_pack_calculation_handler_is_byte_identical_to_legacy(tmp_path: Path):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )

    def _outcome(host):
        return asyncio.run(
            host.dispatch(
                "Calculation", {"expression": "6*7"},
                request_digest="r", tool_call_id="c",
            )
        )

    legacy = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path, exposed_tool_names=("Calculation",),
        now_ms=lambda: 1_700_000_000_000,
    )
    handlers = _bundled_handlers()
    packed = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path, exposed_tool_names=("Calculation",),
        now_ms=lambda: 1_700_000_000_000,
        workspace_handlers={"Calculation": handlers.resolve("Calculation")},
    )
    a, b = _outcome(legacy), _outcome(packed)
    assert a.status == b.status == "ok"
    assert a.output_preview == b.output_preview == {"value": 42}
    assert a.receipt.bounded_output_digest == b.receipt.bounded_output_digest


def test_pack_file_edit_handler_matches_legacy_including_read_ledger(tmp_path: Path, monkeypatch):
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")
    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )
    handlers = _bundled_handlers()
    assert callable(handlers.resolve("FileEdit"))

    async def run(host, workspace: Path):
        (workspace / "f.txt").write_text("alpha beta\n", encoding="utf-8")
        # Fresh full read first so the read ledger allows the edit.
        await host.dispatch("FileRead", {"path": "f.txt"},
                            request_digest="r0", tool_call_id="c0")
        return await host.dispatch(
            "FileEdit", {"path": "f.txt", "oldText": "alpha", "newText": "gamma"},
            request_digest="r1", tool_call_id="c1",
        )

    ws_a, ws_b = tmp_path / "a", tmp_path / "b"
    ws_a.mkdir(); ws_b.mkdir()
    legacy = Gate5BFullToolHost(
        config=config, workspace_root=ws_a,
        exposed_tool_names=("FileRead", "FileEdit"),
        now_ms=lambda: 1_700_000_000_000, read_ledger_enabled=True,
    )
    packed = Gate5BFullToolHost(
        config=config, workspace_root=ws_b,
        exposed_tool_names=("FileRead", "FileEdit"),
        now_ms=lambda: 1_700_000_000_000, read_ledger_enabled=True,
        workspace_handlers={"FileEdit": handlers.resolve("FileEdit")},
    )
    a = asyncio.run(run(legacy, ws_a))
    b = asyncio.run(run(packed, ws_b))
    assert a.status == b.status == "ok"
    assert a.receipt.bounded_output_digest == b.receipt.bounded_output_digest
    assert (ws_a / "f.txt").read_text() == (ws_b / "f.txt").read_text() == "gamma beta\n"


def test_pack_file_edit_blocks_without_fresh_read_like_legacy(tmp_path: Path, monkeypatch):
    """The ledger BLOCK path must also be identical: pack handler calls
    view.enforce_read_before_mutation, so an edit without a prior full read
    yields the same read_ledger_no_prior_read non-recorded block."""
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")
    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )
    handlers = _bundled_handlers()
    (tmp_path / "g.txt").write_text("alpha beta\n", encoding="utf-8")
    packed = Gate5BFullToolHost(
        config=config, workspace_root=tmp_path,
        exposed_tool_names=("FileRead", "FileEdit"),
        now_ms=lambda: 1_700_000_000_000, read_ledger_enabled=True,
        workspace_handlers={"FileEdit": handlers.resolve("FileEdit")},
    )
    outcome = asyncio.run(
        packed.dispatch(
            "FileEdit", {"path": "g.txt", "oldText": "alpha", "newText": "gamma"},
            request_digest="r", tool_call_id="c",
        )
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "read_ledger_no_prior_read"
    assert (tmp_path / "g.txt").read_text() == "alpha beta\n"


def test_pack_file_edit_fuzzy_cascade_matches_legacy(tmp_path: Path, monkeypatch):
    """Fuzzy path parity: same matchTier/matchConfidence keys, same receipt
    digest, and the EditMatch evidence receipt is built by the unchanged
    dispatch envelope (view.store_edit_match_result hand-back)."""
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolHost,
        Gate5BFullToolHostConfig,
    )

    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")
    config = Gate5BFullToolHostConfig.model_validate(
        {"enabled": True, "killSwitchEnabled": False, "routeAttachmentEnabled": True,
         "environment": "local", "environmentAllowlist": ["local"],
         "maxToolCallsPerTurn": 8}
    )
    handlers = _bundled_handlers()

    async def run(host, workspace: Path):
        (workspace / "h.txt").write_text("alpha beta\n", encoding="utf-8")
        return await host.dispatch(
            # Trailing-whitespace variant exercises a fuzzy (non-exact) tier.
            "FileEdit", {"path": "h.txt", "oldText": "alpha ", "newText": "gamma "},
            request_digest="r", tool_call_id="c",
        )

    ws_a, ws_b = tmp_path / "a", tmp_path / "b"
    ws_a.mkdir(); ws_b.mkdir()
    legacy = Gate5BFullToolHost(
        config=config, workspace_root=ws_a, exposed_tool_names=("FileEdit",),
        now_ms=lambda: 1_700_000_000_000,
    )
    packed = Gate5BFullToolHost(
        config=config, workspace_root=ws_b, exposed_tool_names=("FileEdit",),
        now_ms=lambda: 1_700_000_000_000,
        workspace_handlers={"FileEdit": handlers.resolve("FileEdit")},
    )
    a = asyncio.run(run(legacy, ws_a))
    b = asyncio.run(run(packed, ws_b))
    assert a.status == b.status == "ok"
    assert a.output_preview == b.output_preview
    assert a.receipt.bounded_output_digest == b.receipt.bounded_output_digest
    assert (a.edit_match_receipt is None) == (b.edit_match_receipt is None)
    assert (ws_a / "h.txt").read_text() == (ws_b / "h.txt").read_text()
