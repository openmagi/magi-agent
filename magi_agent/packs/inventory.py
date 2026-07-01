"""PR-P3: installed-pack inventory for the dashboard Packs tab.

A read-only projection over pack discovery so the operator can finally see WHAT
each installed pack contributes (its `provides` entries), not just a pack id.
Pure over the discovery layer (`discover_pack_files` + `resolve_enabled_packs`)
so it is trivially testable and never touches the runtime hot path.
"""

from __future__ import annotations

from typing import Any

from magi_agent.packs.discovery import (
    _bundled_firstparty_base,
    default_search_bases,
    discover_pack_files,
    load_packs_config,
    resolve_enabled_packs,
)


def installed_packs_view() -> list[dict[str, Any]]:
    """Return installed packs (first-party + user) with their provided refs.

    Each entry: ``{packId, displayName, description, version, origin, enabled,
    defaultEnabled, provides: [{type, ref}]}``. ``origin`` is ``"first_party"``
    for bundled packs, else ``"user"``. ``enabled`` reflects the post-config
    resolution (config.toml ``[packs] disable`` / ``default_enabled=false`` are
    honored). Duplicate ``pack_id`` across bases is de-duped keeping the
    last-precedence (override-winning) manifest.
    """
    bases = default_search_bases()
    bundled = _bundled_firstparty_base()
    discovered = discover_pack_files(bases)
    enabled_ids = {d.manifest.pack_id for d in resolve_enabled_packs(discovered, load_packs_config())}

    # Later bases win (override contract); keep the last manifest per pack_id.
    by_id: dict[str, dict[str, Any]] = {}
    for d in discovered:
        m = d.manifest
        try:
            is_first_party = d.path.is_relative_to(bundled)
        except (ValueError, OSError):
            is_first_party = False
        by_id[m.pack_id] = {
            "packId": m.pack_id,
            "displayName": m.display_name,
            "description": m.description,
            "version": m.version,
            "origin": "first_party" if is_first_party else "user",
            "defaultEnabled": m.default_enabled,
            "enabled": m.pack_id in enabled_ids,
            "provides": [{"type": p.type, "ref": p.ref} for p in m.provides],
        }
    # Stable output: first-party first, then user; alphabetical within each.
    return sorted(
        by_id.values(),
        key=lambda p: (0 if p["origin"] == "first_party" else 1, p["packId"]),
    )
