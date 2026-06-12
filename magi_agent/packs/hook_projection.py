"""Expose the previously-unexposed ``HookRegistry`` discovery into the live
``HookBus``.

The keystone gap this closes: ``HookRegistry`` existed but had NO discovery seam
that loaded user hook manifests into the live ``HookBus``. This projector turns
the pack hook registry + handler map into the ``RegisteredHook`` tuple the
``HookBus`` consumes, so a user/first-party callback authored as a pack fires
live through the same bus the ADK callback adapter drives.
"""
from __future__ import annotations

from magi_agent.hooks.bus import RegisteredHook
from magi_agent.packs.registries import PackRegistries


def project_registered_hooks(registries: PackRegistries) -> tuple[RegisteredHook, ...]:
    """Turn the pack hook registry + handler map into the RegisteredHook tuple the
    HookBus consumes — the live callback seam."""
    registered: list[RegisteredHook] = []
    for manifest in registries.hooks.list_all():
        handler = registries.hooks_handler(manifest.name)
        if handler is None:
            continue
        registered.append(RegisteredHook(manifest=manifest, handler=handler))
    return tuple(registered)
