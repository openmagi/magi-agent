"""Task 3.6 — §1 micro-assertions: the first-party validator holds NO privilege.

  * It is registered through the IDENTICAL discover -> load -> registry path a user
    pack uses (no hardcoded shortcut), and its impl takes exactly one positional
    param (the typed ``ValidatorCtx`` — no god-object, no privileged kwargs).
  * Its ref must NOT be a string literal on the live path in ``real_runner.py``; it
    arrives via the pack catalog (``_loaded_pack_validator_refs``). The live ref uses
    the ``verifier:`` prefix (the recognized public-ref prefix).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import magi_agent
from magi_agent.packs.context import PrimitiveType
from magi_agent.packs.discovery import discover_pack_files
from magi_agent.packs.loader import load_packs
from magi_agent.packs.registries import PrimitiveRegistry, RegistryRegistrationSink

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_REF = "verifier:sourceOpened@1"


def test_first_party_validator_has_no_privileged_registration() -> None:
    discovered = discover_pack_files([_FIRST_PARTY_ROOT])
    registry = PrimitiveRegistry()
    load_packs(discovered, RegistryRegistrationSink(registry))
    impl = registry.resolve(_REF, ptype=PrimitiveType.VALIDATOR)
    sig = inspect.signature(impl)
    params = [
        p for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert len(params) == 1  # only the typed ValidatorCtx


def test_no_hardcoded_first_party_validator_in_real_runner() -> None:
    src = (Path(magi_agent.__file__).parent / "cli" / "real_runner.py").read_text()
    # The bundled validator ref must arrive via the pack catalog, never as a literal.
    assert _REF not in src
    assert "sourceOpened" not in src
