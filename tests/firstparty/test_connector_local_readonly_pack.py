"""Group D.1 — bundled first-party ``connector_local_readonly`` pack registers a
``ConnectorSpec`` (server ref + projected ToolManifests) via the typed
``ConnectorProvideContext`` (D5)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_REF = "connector:local-readonly@1"


def test_connector_local_readonly_pack_registers_connector() -> None:
    registries, report = load_into_registries([_FIRST_PARTY_ROOT / "connector_local_readonly"])
    assert _REF in report.registered
    spec = registries.connectors.resolve(_REF)
    assert spec is not None
    assert spec.server_ref == "local-readonly"
    assert spec.readonly is True
    assert any(m.name == "LocalSourceOpen" for m in spec.tool_manifests)
