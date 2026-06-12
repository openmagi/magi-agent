"""Group A.1 — bundled first-party ``tools_clock`` pack registers ``Clock`` via the
typed ``ToolProvideContext`` (D5), no privileged kwargs.

Adapted to the REAL Phase-1/2/3 ABI (the plan doc's ``PackRegistries.empty()`` /
``discover_packs`` / ``firstparty_packs_dir`` symbols drifted): discovery is
``discover_pack_files`` + ``resolve_enabled_packs`` and projection into the live
registries is :func:`load_into_registries`.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_tools_clock_pack_registers_clock_via_typed_context() -> None:
    pack_dir = _FIRST_PARTY_ROOT / "tools_clock"
    registries, report = load_into_registries([pack_dir])
    assert "Clock" in report.registered
    manifest = registries.tools.resolve("Clock")
    assert manifest is not None
    assert manifest.name == "Clock"
    assert manifest.permission == "meta"
    assert "plan" in manifest.available_in_modes and "act" in manifest.available_in_modes
