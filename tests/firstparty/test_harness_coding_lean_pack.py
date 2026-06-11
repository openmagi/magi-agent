"""Group E.1 — bundled first-party ``harness_coding_lean`` pack registers a
``ResolvedHarnessPack`` via the typed ``HarnessProvideContext`` (D5)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_REF = "harness:coding-lean@1"


def test_harness_coding_lean_pack_registers_pack() -> None:
    registries, report = load_into_registries([_FIRST_PARTY_ROOT / "harness_coding_lean"])
    assert _REF in report.registered
    pack = registries.harnesses.resolve(_REF)
    assert pack is not None
    assert pack.enabled is True
    assert "FileEdit" in pack.components["tools"]
