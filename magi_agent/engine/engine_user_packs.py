"""User-pack gate helpers, pure move out of engine/driver.py (PR-G1).

These two helpers run USER VALIDATOR / EVIDENCE_PRODUCER pack impls at the
pre-final gate. They are self-state free (every dependency is a function-local
lazy import), so they live here as module-level functions and the driver keeps
thin delegating methods for surface compatibility. Bodies are moved verbatim;
only the ``self`` parameter is dropped.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_user_validators(
    *,
    required_validators: tuple[str, ...],
    observed_public_refs: set[str],
    session_id: str,
    turn_id: str,
    final_text: str,
) -> list[dict[str, object]]:
    """Execute user VALIDATOR pack impls over the produced artifact (PR2).

    Default OFF: returns ``[]`` and never imports the pack pipeline, so the
    caller's gate payload is byte-identical to before. When ON, for each
    required validator ref whose impl is loaded, build a ``ValidatorCtx`` over
    the produced artifact, call the impl, read its verdict, and:

    * a PASSING verdict adds the ref to ``observed_public_refs`` (in place) so
      ``required_validators`` is satisfied for that ref;
    * a FAILING verdict leaves the ref missing (the caller blocks) and the
      detail is returned for the bus payload.

    Fail-closed: an impl that raises is treated as a failing verdict with an
    error detail, so a broken user validator blocks rather than silently
    passing. Returns the list of verdict dicts (``ref``/``passed``/``detail``)
    for surfacing on ``verifierBus``.
    """
    from magi_agent.config.env import user_validator_packs_enabled  # noqa: PLC0415

    if not user_validator_packs_enabled():
        return []
    if not required_validators:
        return []
    from magi_agent.packs.user_validators import (  # noqa: PLC0415
        loaded_user_validator_impls,
    )

    impls = loaded_user_validator_impls()
    if not impls:
        return []

    from magi_agent.packs.context import (  # noqa: PLC0415
        SessionReadView,
        ValidatorCtx,
    )

    # Defense-in-depth (not isolation): when capability enforcement is ON,
    # hand each USER validator impl the RESTRICTED capability set for its
    # primitive type so a pack that reaches outside its declared role through
    # the typed context surface raises CapabilityError (caught below as a
    # failing verdict -> fail-closed). OFF: pass None (full set, byte-id).
    from magi_agent.config.env import (  # noqa: PLC0415
        pack_capability_enforcement_enabled,
    )

    restricted_caps = None
    if pack_capability_enforcement_enabled():
        from magi_agent.packs.context import (  # noqa: PLC0415
            PrimitiveType,
            restricted_capabilities_for,
        )

        restricted_caps = restricted_capabilities_for(PrimitiveType.VALIDATOR)

    artifact: dict[str, object] = {
        "finalText": final_text,
        "sessionId": session_id,
        "turnId": turn_id,
    }
    session = SessionReadView(
        invocation_id=session_id,
        agent_name="magi",
        turn_index=0,
    )
    verdicts: list[dict[str, object]] = []
    for ref in required_validators:
        impl = impls.get(ref)
        if impl is None:
            # A required validator with no loaded impl (e.g. a first-party
            # recipe validator) is left for the existing observe paths.
            continue
        ctx = ValidatorCtx(ref=ref, artifact=artifact, session=session,
                           capabilities=restricted_caps)
        try:
            impl(ctx)
            verdict = ctx.verdict()
        except Exception as exc:  # noqa: BLE001 - a broken validator blocks
            verdicts.append(
                {"ref": ref, "passed": False,
                 "detail": f"validator impl raised: {exc}"}
            )
            continue
        if verdict is None:
            # No emit() call ⇒ treat as a fail-closed block (the impl owed a
            # verdict and produced none).
            verdicts.append(
                {"ref": ref, "passed": False,
                 "detail": "validator emitted no verdict"}
            )
            continue
        verdicts.append(
            {"ref": ref, "passed": verdict.passed, "detail": verdict.detail}
        )
        if verdict.passed:
            observed_public_refs.add(ref)
    return verdicts


def run_user_evidence_producers(
    *,
    required_evidence: tuple[str, ...],
    observed_public_refs: set[str],
    session_id: str,
    turn_id: str,
) -> list[dict[str, object]]:
    """Run user EVIDENCE_PRODUCER pack runtime emitters at the gate (PR3).

    Default OFF: returns ``[]`` and never imports the pack pipeline, so the
    caller's gate payload is byte-identical to before. When ON, for each
    required evidence ref that a loaded USER producer provides (keyed by its
    ``ProducerSpec.public_ref``), build an ``EvidenceProducerCtx`` over the
    live session, call the pack's optional ``emit_evidence`` runtime emitter,
    and for every record it emits whose ``evidence_type`` matches the spec,
    add the spec's ``public_ref`` to ``observed_public_refs`` (in place) so
    ``required_evidence`` is satisfied for that ref.

    Fail-safe: a producer whose emitter raises (or that ships no emitter, or
    emits nothing) leaves the ref unobserved (the caller blocks) and never
    crashes the turn. Returns the list of emitted-record dicts
    (``ref``/``evidenceType``/``payload``) for surfacing on ``verifierBus``.
    """
    from magi_agent.config.env import user_evidence_packs_enabled  # noqa: PLC0415

    if not user_evidence_packs_enabled():
        return []
    if not required_evidence:
        return []
    from magi_agent.packs.user_evidence import (  # noqa: PLC0415
        loaded_user_evidence_producers,
    )

    producers = loaded_user_evidence_producers()
    if not producers:
        return []

    from magi_agent.packs.context import (  # noqa: PLC0415
        EvidenceProducerCtx,
        SessionReadView,
    )

    # Defense-in-depth (not isolation): restrict USER evidence_producer impls
    # to their primitive's capability set when enforcement is ON. OFF: None
    # (full set, byte-identical). A raise is caught below as "emit nothing".
    from magi_agent.config.env import (  # noqa: PLC0415
        pack_capability_enforcement_enabled,
    )

    restricted_caps = None
    if pack_capability_enforcement_enabled():
        from magi_agent.packs.context import (  # noqa: PLC0415
            PrimitiveType,
            restricted_capabilities_for,
        )

        restricted_caps = restricted_capabilities_for(
            PrimitiveType.EVIDENCE_PRODUCER
        )

    session = SessionReadView(
        invocation_id=session_id,
        agent_name="magi",
        turn_index=0,
    )
    emitted: list[dict[str, object]] = []
    for ref in required_evidence:
        producer = producers.get(ref)
        if producer is None:
            # A required evidence ref with no loaded USER producer (e.g. a
            # first-party activity ref) is left for the existing emit paths.
            continue
        if producer.emitter is None:
            # Declarative-only pack: no runtime code to run (pre-PR3 shape).
            continue
        ctx = EvidenceProducerCtx(session=session, capabilities=restricted_caps)
        try:
            producer.emitter(ctx)
        except Exception as exc:  # noqa: BLE001 - a broken producer must not crash
            logger.warning(
                "user evidence producer %s raised; emitting nothing: %s",
                producer.ref,
                exc,
            )
            continue
        for record in ctx.emitted():
            if record.get("evidence_type") != producer.spec.evidence_type:
                continue
            observed_public_refs.add(producer.spec.public_ref)
            emitted.append(
                {
                    "ref": producer.spec.public_ref,
                    "evidenceType": producer.spec.evidence_type,
                    "payload": record.get("payload", {}),
                }
            )
    return emitted
