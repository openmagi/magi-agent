"""Load user-authored EVIDENCE_PRODUCER pack runtime emitters (PR3).

A user ``evidence_producer`` ref already reaches the pre-final gate's evidence
vocabulary (its static manifest ref is read by
:func:`magi_agent.evidence.first_party_gate.enabled_first_party_activity_refs`),
but the producer's ``provide`` impl only registers a DECLARATIVE ``ProducerSpec``
(``evidence_type`` -> ``public_ref``); no runtime code ever runs to actually EMIT
an evidence record into the live turn. So a required ``evidence:`` ref a user
pack declares could never be OBSERVED (enabling a user producer could ONLY ever
block). This module is the Phase-3 activation half: it discovers + loads USER
evidence_producer packs and, for each, returns the declarative ``ProducerSpec``
plus the pack's OPTIONAL runtime emitter so the engine can run it and observe the
emitted ``public_ref``.

ABI (additive, minimal, no manifest/loader/registry change): a user evidence
pack keeps authoring the documented ``provide(EvidenceProducerProvideContext)``
that registers a ``ProducerSpec``, and opts into RUNTIME emission by exposing an
OPTIONAL module-level ``emit_evidence(EvidenceProducerCtx) -> None`` symbol next
to ``provide`` (same module). The emitter reads the live session and calls
``ctx.emit(evidence_type=..., payload=...)``. We map each emitted record's
``evidence_type`` back to the spec's ``public_ref`` and contribute it to the
gate's observed set. A pack that ships no ``emit_evidence`` symbol is
declarative-only and emits nothing at runtime (today's behavior, byte-identical).

Scope: ONLY user-origin evidence_producer impls are returned. First-party
producer refs (e.g. ``evidence:toolCall@1``) already have a dedicated runtime
emission path (``build_first_party_activities`` driven by tool dispatch); their
modules expose no ``emit_evidence`` symbol, so they are filtered out by pack-id
origin and would be inert here regardless. Last-wins override still applies: a
user pack that re-declares a first-party ref makes that ref user-origin.

Additive + fail-open: any discovery/load error collapses to an empty map so the
gate falls back to its pre-PR3 (declarative-only) behavior rather than crashing
the turn. Mirrors ``loaded_user_validator_impls`` (PR2).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.context import EvidenceProducerCtx, ProducerSpec

_LOGGER = logging.getLogger(__name__)

EvidenceEmitter = Callable[["EvidenceProducerCtx"], None]

# The optional runtime-emitter symbol an evidence_producer impl module may expose
# next to ``provide`` to opt into runtime emission (PR3 ABI).
_EMITTER_SYMBOL = "emit_evidence"


@dataclass(frozen=True)
class UserEvidenceProducer:
    """A loaded USER evidence_producer ready to emit at runtime.

    ``spec`` is the declarative ``ProducerSpec`` the pack's ``provide`` registered
    (carries ``evidence_type`` -> ``public_ref``). ``emitter`` is the optional
    ``emit_evidence(EvidenceProducerCtx) -> None`` runtime callable; ``None`` when
    the pack is declarative-only (no runtime emission).
    """

    ref: str
    spec: "ProducerSpec"
    emitter: EvidenceEmitter | None


def _user_origin_evidence_refs(enabled: "list") -> frozenset[str]:
    """Evidence_producer refs whose LAST-declaring pack is a USER pack.

    Manifest-level only (no impl import). Mirrors ``user_validators``'s
    origin classification and ``PrimitiveRegistry``'s first-party prefix test.
    """
    from magi_agent.packs.registries import _FIRST_PARTY_PACK_ID_PREFIX  # noqa: PLC0415

    last_is_user: dict[str, bool] = {}
    for disc in enabled:
        is_user = not disc.manifest.pack_id.startswith(_FIRST_PARTY_PACK_ID_PREFIX)
        for entry in disc.manifest.provides:
            if entry.type == "evidence_producer":
                last_is_user[entry.ref] = is_user
    return frozenset(ref for ref, is_user in last_is_user.items() if is_user)


def loaded_user_evidence_producers(
    bases: "list[Path] | None" = None,
) -> dict[str, UserEvidenceProducer]:
    """Discover + load USER evidence_producer packs, keyed by public ref.

    Returns ``{public_ref: UserEvidenceProducer}`` for every loaded USER
    ``evidence_producer`` provider (first-party refs excluded; see module
    docstring). The engine only RUNS a producer whose ``public_ref`` is actually
    required this turn. The map is keyed by ``ProducerSpec.public_ref`` (the ref
    that lands in ``observed_public_refs``), which may differ from the manifest
    registration ref under override.
    """
    from magi_agent.packs.context import (  # noqa: PLC0415
        EvidenceProducerProvideContext,
        ProducerSpec,
    )
    from magi_agent.packs.discovery import (  # noqa: PLC0415
        default_search_bases,
        discover_pack_files,
        load_packs_config,
        resolve_enabled_packs,
    )
    from magi_agent.packs.loader import RecordingSink, load_packs  # noqa: PLC0415

    search_bases = list(bases) if bases is not None else list(default_search_bases())
    try:
        discovered = discover_pack_files(search_bases)
        enabled = resolve_enabled_packs(discovered, load_packs_config())
        user_refs = _user_origin_evidence_refs(enabled)
        if not user_refs:
            return {}
        sink = RecordingSink()
        load_packs(enabled, sink)
    except Exception:  # noqa: BLE001 - a malformed pack must not break the gate
        _LOGGER.warning(
            "user evidence pack discovery failed; loading none", exc_info=True
        )
        return {}

    producers: dict[str, UserEvidenceProducer] = {}
    for primitive in sink.registered:
        if primitive.type != "evidence_producer":
            continue
        if primitive.ref not in user_refs:
            continue
        provide = primitive.impl
        if not callable(provide):
            continue
        try:
            captured: list[ProducerSpec] = []

            def _register(_ref: str, spec: "ProducerSpec") -> None:
                captured.append(spec)

            provide(EvidenceProducerProvideContext(register=_register))
        except Exception:  # noqa: BLE001 - a broken provide must not break the gate
            _LOGGER.warning(
                "user evidence producer provide failed for %s; skipping",
                primitive.ref,
                exc_info=True,
            )
            continue
        if not captured:
            continue
        spec = captured[-1]  # last-wins within a single provide call
        emitter = _resolve_emitter(provide)
        # Key by public_ref: that is the ref the engine adds to
        # observed_public_refs and matches against required_evidence.
        producers[spec.public_ref] = UserEvidenceProducer(
            ref=primitive.ref, spec=spec, emitter=emitter
        )
    return producers


def _resolve_emitter(provide: Callable[..., object]) -> EvidenceEmitter | None:
    """Reflect the OPTIONAL ``emit_evidence`` symbol from ``provide``'s module.

    Returns ``None`` (declarative-only pack) when the module exposes no callable
    ``emit_evidence``. Never raises: a reflection failure degrades to no emitter.
    """
    import importlib  # noqa: PLC0415

    module_name = getattr(provide, "__module__", None)
    if not module_name:
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - reflection is best-effort
        return None
    emitter = getattr(module, _EMITTER_SYMBOL, None)
    return emitter if callable(emitter) else None


__all__ = [
    "EvidenceEmitter",
    "UserEvidenceProducer",
    "loaded_user_evidence_producers",
]
