"""PR3: a user-authored EVIDENCE_PRODUCER pack EMITS at runtime, default-OFF.

A user ``evidence_producer`` pack is read today for its STATIC manifest refs only
(``enabled_first_party_activity_refs``); its ``provide`` impl registers a
declarative ``ProducerSpec`` but no runtime code ever runs to actually EMIT an
evidence record into the live turn. So a required ``evidence:`` ref a user pack
declares can never be OBSERVED at the pre-final gate (enabling it could ONLY ever
block). This pins the Phase-3 activation contract:

- Flag OFF (default): the producer's runtime emitter is NEVER called, no user
  evidence is emitted, and the gate payload is byte-identical to before: a
  required-but-unobserved user evidence ref still blocks.
- Flag ON (``MAGI_USER_EVIDENCE_PACKS_ENABLED=1``): the producer's runtime
  emitter runs over the live session, calls ``ctx.emit(...)``, and the emitted
  record's ``public_ref`` lands in ``observed_public_refs`` / the gate's
  ``matchedRefs``, satisfying the required evidence ref so the turn is NOT
  blocked.
- A producer emitter that RAISES must never crash the turn (fail-safe): the ref
  simply stays unobserved and the turn blocks as if nothing emitted.

ABI: the user pack authors the declarative ``provide`` the documented way (a
``ProducerSpec``), and opts into RUNTIME emission by exposing an optional
module-level ``emit_evidence(EvidenceProducerCtx) -> None`` symbol next to
``provide``. First-party producer modules expose no such symbol, so first-party
behavior stays byte-identical.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly

_EVIDENCE_REF = "evidence:user-snapshot@1"
_EVIDENCE_TYPE = "UserSnapshot"

_PACK_TOML = """\
packId = "user.user-snapshot-pack"
displayName = "User Snapshot Pack"
version = "0.1.0"
description = "User-authored evidence producer pack for the PR3 emission test."

[[provides]]
type = "evidence_producer"
ref = "evidence:user-snapshot@1"
impl = "user_snapshot_pack.impl:provide"
"""

# ``provide`` registers the declarative ProducerSpec (today's behavior).
# ``emit_evidence`` is the optional RUNTIME emitter PR3 invokes when ON.
_IMPL_PY = '''\
"""User evidence producer impl: declarative spec + runtime emitter."""
from __future__ import annotations

from magi_agent.packs.context import (
    EvidenceProducerCtx,
    EvidenceProducerProvideContext,
    ProducerSpec,
)

# Module-level call counter so the test can prove the emitter never runs when OFF.
EMIT_CALLS: list[str] = []


def provide(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:user-snapshot@1",
        ProducerSpec(
            evidence_type="UserSnapshot",
            public_ref="evidence:user-snapshot@1",
            producer_surfaces=("tool_host",),
        ),
    )


def emit_evidence(ctx: EvidenceProducerCtx) -> None:
    EMIT_CALLS.append("emit")
    ctx.emit(
        evidence_type="UserSnapshot",
        payload={"sessionId": ctx.session.invocation_id, "note": "snapshot taken"},
    )
'''

# A producer whose emitter raises: the turn must not crash and the ref stays
# unobserved (so the turn blocks). Uses its OWN pack/module name so the loader's
# sys.modules-by-top-level-name cache cannot return the non-raising impl from a
# sibling test (the documented unique-pack-dir-name requirement).
_RAISING_PACK_TOML = """\
packId = "user.user-snapshot-raise-pack"
displayName = "User Snapshot Raise Pack"
version = "0.1.0"
description = "User evidence producer whose emitter raises (PR3 fail-safe test)."

[[provides]]
type = "evidence_producer"
ref = "evidence:user-snapshot@1"
impl = "user_snapshot_raise_pack.impl:provide"
"""

_RAISING_IMPL_PY = '''\
"""User evidence producer whose runtime emitter raises."""
from __future__ import annotations

from magi_agent.packs.context import (
    EvidenceProducerCtx,
    EvidenceProducerProvideContext,
    ProducerSpec,
)


def provide(context: EvidenceProducerProvideContext) -> None:
    context.register(
        "evidence:user-snapshot@1",
        ProducerSpec(
            evidence_type="UserSnapshot",
            public_ref="evidence:user-snapshot@1",
            producer_surfaces=("tool_host",),
        ),
    )


def emit_evidence(ctx: EvidenceProducerCtx) -> None:
    raise RuntimeError("boom in user producer emitter")
'''


def _write_user_evidence_pack(packs_base: Path) -> None:
    pack_dir = packs_base / "user_snapshot_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(_PACK_TOML)
    (pack_dir / "impl.py").write_text(_IMPL_PY)


def _write_raising_evidence_pack(packs_base: Path) -> None:
    pack_dir = packs_base / "user_snapshot_raise_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(_RAISING_PACK_TOML)
    (pack_dir / "impl.py").write_text(_RAISING_IMPL_PY)


def _driver() -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),  # non-dev-coding → gate applies
            evidenceRequirements=(_EVIDENCE_REF,),
            requiredValidators=(),
            missingEvidenceAction="audit",
        ),
        evidence_collector=lambda _turn: (),
    )


def _gate(driver: MagiEngineDriver) -> dict[str, object]:
    payload = driver._pre_final_gate_payload(
        session_id="s-1",
        turn_id="t-1",
        prompt="please finish the task",
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=False,
        repair_attempt_count=0,
        final_text="done",
        live_selected_pack_ids=(),
    )
    assert payload is not None, "pre-final gate did not apply"
    return payload


def _patch_bases(monkeypatch, packs_base: Path) -> None:
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases",
        lambda: [packs_base],
    )


def test_user_evidence_inert_when_flag_off(tmp_path: Path, monkeypatch) -> None:
    packs_base = tmp_path / "packs"
    _write_user_evidence_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.delenv("MAGI_USER_EVIDENCE_PACKS_ENABLED", raising=False)

    payload = _gate(_driver())

    # OFF path is byte-identical to before: the required user evidence ref is
    # unobserved (its emitter never ran), so the turn blocks.
    assert payload["decision"] == "block"
    assert _EVIDENCE_REF in payload["missingEvidence"]
    # Prove the emitter was never imported/executed.
    import sys

    impl_mod = sys.modules.get("user_snapshot_pack.impl")
    if impl_mod is not None:
        assert impl_mod.EMIT_CALLS == [], "producer emitter ran while flag OFF"


def test_user_evidence_emits_and_satisfies_required_ref_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_user_evidence_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.setenv("MAGI_USER_EVIDENCE_PACKS_ENABLED", "1")

    payload = _gate(_driver())

    # The producer ran, emitted, so its public_ref is observed → not missing.
    assert _EVIDENCE_REF not in payload["missingEvidence"]
    assert payload["decision"] == "pass"
    assert _EVIDENCE_REF in payload["matchedRefs"]

    import sys

    impl_mod = sys.modules["user_snapshot_pack.impl"]
    assert impl_mod.EMIT_CALLS == ["emit"], "producer emitter did not run once"


def test_user_evidence_raising_emitter_does_not_crash_and_still_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_raising_evidence_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.setenv("MAGI_USER_EVIDENCE_PACKS_ENABLED", "1")

    # Must not raise: a broken producer is fail-safe.
    payload = _gate(_driver())

    # Nothing emitted → ref stays unobserved → turn blocks.
    assert payload["decision"] == "block"
    assert _EVIDENCE_REF in payload["missingEvidence"]
