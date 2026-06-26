"""Load user-authored VALIDATOR pack impls for the pre-final gate (PR2).

A user ``validator`` ref already reaches the enforce gate (its static manifest
ref is appended to ``required_validators`` by ``real_runner``), but the
validator's own impl ``(ValidatorCtx) -> ValidatorVerdict | None`` was never
executed, so the ref could never be OBSERVED (enabling a user validator could
ONLY ever block). This module is the Phase-3 activation half: it discovers + loads
USER validator packs through the EXISTING pack pipeline
(:func:`load_into_registries`, which projects each validator impl into
``registries.validators`` via the new ``validator`` branch of
``project_into_registries``) and returns the keyed impl map so the engine can run
a required validator over the produced artifact and read its verdict.

Scope: ONLY user-origin validator impls are returned. First-party validator refs
(e.g. ``verifier:sourceOpened@1``) already have dedicated engine observe paths;
running their (Phase-2 self-contained) impls here over a different artifact would
change existing behavior, so they are filtered out by pack-id origin. The filter
mirrors ``PrimitiveRegistry``'s origin classification (a first-party pack id
prefix). Last-wins override still applies: a user pack that re-declares a
first-party validator ref makes that ref user-origin and IS returned.

Additive + fail-open: any discovery/load error collapses to an empty map so the
gate falls back to its pre-PR2 (block-only) behavior rather than crashing the
turn. Mirrors ``merge_user_tool_packs`` (PR1).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.context import ValidatorCtx, ValidatorVerdict

_LOGGER = logging.getLogger(__name__)

ValidatorImpl = Callable[["ValidatorCtx"], "ValidatorVerdict | None"]


def _user_origin_validator_refs(bases: "list[Path]") -> frozenset[str]:
    """Validator refs declared by USER (non-first-party) packs, last-wins origin.

    Manifest-level discovery only (no impl import): iterate enabled packs in
    resolved (base/last-wins) order and record, per validator ref, whether the
    LAST pack to declare it is a user pack. Mirrors ``real_runner``'s
    ``_loaded_pack_validator_refs`` discovery and ``PrimitiveRegistry``'s
    first-party origin classification.
    """
    from magi_agent.packs.discovery import (  # noqa: PLC0415
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )
    from magi_agent.packs.registries import _FIRST_PARTY_PACK_ID_PREFIX  # noqa: PLC0415
    from magi_agent.packs.signing import filter_trusted_packs  # noqa: PLC0415

    discovered = discover_pack_files(list(bases))
    enabled = resolve_enabled_packs(discovered, load_packs_config())
    # Curated-trust gate (model A): drop untrusted user packs when signing is
    # required so an untrusted validator pack is never treated as a user ref
    # (and load_into_registries below also re-applies the same filter).
    enabled = filter_trusted_packs(enabled)
    last_is_user: dict[str, bool] = {}
    for disc in enabled:
        is_user = not disc.manifest.pack_id.startswith(_FIRST_PARTY_PACK_ID_PREFIX)
        for entry in disc.manifest.provides:
            if entry.type == "validator":
                last_is_user[entry.ref] = is_user
    return frozenset(ref for ref, is_user in last_is_user.items() if is_user)


def loaded_user_validator_impls(
    bases: "list[Path] | None" = None,
) -> dict[str, ValidatorImpl]:
    """Discover + load USER validator impls from disk-discovered packs, keyed by ref.

    Returns ``{validator_ref: impl}`` for every loaded USER ``validator`` provider
    (first-party refs are excluded; see module docstring). The engine only RUNS a
    returned impl whose ref is actually required this turn.
    """
    from magi_agent.packs.discovery import default_search_bases  # noqa: PLC0415
    from magi_agent.packs.registries import load_into_registries  # noqa: PLC0415

    search_bases = list(bases) if bases is not None else list(default_search_bases())
    try:
        user_refs = _user_origin_validator_refs(search_bases)
        if not user_refs:
            return {}
        pack_registries, _report = load_into_registries(search_bases)
    except Exception:  # noqa: BLE001 - a malformed pack must not break the gate
        _LOGGER.warning(
            "user validator pack discovery failed; loading none", exc_info=True
        )
        return {}

    impls: dict[str, ValidatorImpl] = {}
    for ref in pack_registries.validators.list_refs():
        if ref not in user_refs:
            continue
        impl = pack_registries.validators.resolve(ref)
        if callable(impl):
            impls[ref] = impl
    return impls


__all__ = ["loaded_user_validator_impls", "ValidatorImpl"]
