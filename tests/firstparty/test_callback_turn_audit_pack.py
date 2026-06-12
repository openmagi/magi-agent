"""Group F.1 — bundled first-party ``callback_turn_audit`` pack registers a
``HookManifest`` + handler via the typed ``CallbackProvideContext`` (D5). The
manifest lands in ``registries.hooks`` (HookRegistry) and the handler in the
parallel handler map (``registries.hooks_handler``)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.hooks.manifest import HookPoint
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_callback_turn_audit_pack_registers_hook() -> None:
    registries, report = load_into_registries([_FIRST_PARTY_ROOT / "callback_turn_audit"])
    assert "turn-audit" in report.registered
    manifest = registries.hooks.resolve("turn-audit")
    assert manifest is not None
    assert manifest.point is HookPoint.BEFORE_TURN_START
    assert registries.hooks_handler("turn-audit") is not None
