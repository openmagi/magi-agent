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
