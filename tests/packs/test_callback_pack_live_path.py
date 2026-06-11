"""Group F.3 — a pack callback fires LIVE through the ``HookBus`` (the keystone:
``HookRegistry`` now has a discovery path into the bus via ``project_registered_hooks``)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookPoint
from magi_agent.packs.hook_projection import project_registered_hooks
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"


def test_pack_callback_fires_through_hook_bus() -> None:
    registries, _ = load_into_registries([_FIRST_PARTY_ROOT / "callback_turn_audit"])
    registered = project_registered_hooks(registries)
    assert any(h.manifest.name == "turn-audit" for h in registered)

    bus = HookBus(hooks=registered)
    result = bus.run(
        point=HookPoint.BEFORE_TURN_START,
        context=HookContext(bot_id="test-bot"),
        harness_state=build_default_resolved_harness_state(),
    )
    # The audit hook is non-blocking and returns continue; the bus must not block.
    assert result.final_action == "continue"
    assert any(r.reason == "turn-audit observed" for r in result.results)
