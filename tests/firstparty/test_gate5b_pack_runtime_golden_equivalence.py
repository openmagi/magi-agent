"""C1 acceptance proof: the pack-loaded gate5b runtime is byte-identical to the
legacy host on the committed golden scenarios.

The goldens in tests/fixtures/gate5b_golden/ were captured BEFORE any C1
decomposition (fully-legacy dispatch). Re-running both scenario drivers with
the bundled pack handlers + tool_host dispatch policies injected and comparing
byte-for-byte against those goldens IS the migration proof: the moved tool
bodies (Clock/Calculation/FileEdit) and the moved policies (memory-mode,
permission-preflight) reproduce the legacy behavior exactly, inside the
unchanged dispatch envelope. Bases are the bundled first-party root
(hermetic — no machine ~/.magi/packs influence).
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from tests.fixtures.gate5b_golden import scenarios
from tests.fixtures.gate5b_golden.capture import golden_path, render

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def _pack_runtime():
    from magi_agent.packs.registries import build_tool_host_runtime_from_packs

    return build_tool_host_runtime_from_packs(bases=[_FIRST_PARTY_ROOT])


def test_pack_loaded_runtime_reproduces_dispatch_ok_golden() -> None:
    handlers, policies = _pack_runtime()
    live = render(
        scenarios.run_dispatch_ok_scenario(
            workspace_handlers=handlers, dispatch_policies=policies
        )
    )
    assert live == golden_path("dispatch_ok").read_text()


def test_pack_loaded_runtime_reproduces_dispatch_blocked_golden() -> None:
    handlers, policies = _pack_runtime()
    live = render(
        scenarios.run_dispatch_blocked_scenario(
            workspace_handlers=handlers, dispatch_policies=policies
        )
    )
    assert live == golden_path("dispatch_blocked").read_text()
