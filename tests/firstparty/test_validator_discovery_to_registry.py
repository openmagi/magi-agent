"""Task 3.2 — discover -> catalog -> registry, end-to-end on the bundled pack.

Adapted to the real Phase-1/2 ABI:
  * discovery: ``discover_pack_files(bases) -> list[DiscoveredPack]``;
  * catalog:   ``load_packs(discovered, sink)`` then ``build_catalog(primitives)``;
  * registry:  ``PrimitiveRegistry`` fed via the ``RegistryRegistrationSink`` adapter
    (the loader's ``RegistrationSink`` protocol bridged onto the keyed registry).

The bundled validator's typed impl is invoked through ``ValidatorCtx`` (Phase-2 ABI).
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.catalog_build import build_catalog
from magi_agent.packs.context import PrimitiveType, SessionReadView, ValidatorCtx
from magi_agent.packs.discovery import discover_pack_files
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import (
    PrimitiveRegistry,
    RegistryRegistrationSink,
)

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_REF = "verifier:sourceOpened@1"


def test_bundled_validator_flows_discovery_to_catalog_to_registry() -> None:
    discovered = discover_pack_files([_FIRST_PARTY_ROOT])
    pack_ids = {d.manifest.pack_id for d in discovered}
    assert "openmagi.source-opened" in pack_ids

    registry = PrimitiveRegistry()
    result = load_packs(discovered, RegistryRegistrationSink(registry))
    catalog = build_catalog(result.primitives)
    assert _REF in catalog.validator_refs

    impl = registry.resolve(_REF, ptype=PrimitiveType.VALIDATOR)
    session = SessionReadView(invocation_id="i", agent_name="a", turn_index=0)

    passed = impl(ValidatorCtx(ref=_REF, artifact={"observedRefs": [_REF]}, session=session))
    failed = impl(ValidatorCtx(ref=_REF, artifact={"observedRefs": []}, session=session))
    assert passed.passed is True
    assert failed.passed is False
