"""Static gate for first-party activity capture.

Mirrors ``cli/real_runner._loaded_pack_validator_refs``: refs are STATIC
manifest data (``provides`` entries of type ``evidence_producer``), read
without importing any pack impl, so an unrelated pack's import error can never
fail-open or fail-closed this gate. ``[packs] disable`` (config.toml) drops a
pack's refs — that is the removability contract: no enabled producer pack, no
capture. ``MAGI_FP_EVIDENCE_DISABLED`` is the ops kill-switch on top.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

FIRST_PARTY_EVIDENCE_DISABLED_ENV = "MAGI_FP_EVIDENCE_DISABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def first_party_evidence_disabled(env: Mapping[str, str] | None = None) -> bool:
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(FIRST_PARTY_EVIDENCE_DISABLED_ENV, "")
    return str(raw).strip().lower() in _TRUTHY


def enabled_first_party_activity_refs(
    *,
    bases: list[Path] | None = None,
) -> tuple[str, ...]:
    """Evidence-producer refs from enabled packs' STATIC manifests.

    Narrow guards only around discovery/parse (missing/empty packs tree ⇒ ()),
    matching the validator-gate convention. Order-stable, deduped.
    """
    try:
        from magi_agent.packs.discovery import (  # noqa: PLC0415
            default_search_bases,
            discover_pack_files,
            load_packs_config,
            resolve_enabled_packs,
        )
    except Exception:
        return ()
    try:
        discovered = discover_pack_files(bases if bases is not None else default_search_bases())
        enabled = resolve_enabled_packs(discovered, load_packs_config())
    except Exception:
        return ()
    refs: list[str] = []
    seen: set[str] = set()
    for disc in enabled:
        for entry in disc.manifest.provides:
            if entry.type == "evidence_producer" and entry.ref not in seen:
                seen.add(entry.ref)
                refs.append(entry.ref)
    return tuple(refs)
