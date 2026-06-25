"""2a: the RESERVED capability seam in ``packs/context.py`` is ENFORCED, default-OFF.

Capability tokens used to be purely advisory: the ``capabilities`` frozenset was
carried on every typed context but NO method ever consulted it. This pins the
contract that each capability-bearing method now raises ``CapabilityError`` when
its required token is absent from ``self.capabilities``, and that the three
USER-pack construction sites pass a RESTRICTED set only when the new
``MAGI_PACK_CAPABILITY_ENFORCEMENT_ENABLED`` flag is ON.

IMPORTANT FRAMING (defense-in-depth, NOT isolation): this is an ABI-surface
contract plus a defense-in-depth check on the documented capability methods. It
is NOT a true isolation boundary -- a malicious impl can still ``import os``,
open files, or call out to the network directly. Real hosted isolation needs
process/container sandboxing (a separate effort). The check only narrows what a
pack can do *through the typed context surface*, so an honest-but-overreaching
pack fails closed instead of silently exceeding its declared role.

Because the DEFAULT capability sets already contain each method's own token, a
context built with defaults is byte-identical to before (never raises). The flag
is OFF by default, so all three construction sites pass NO ``capabilities=`` (the
full default set), keeping the OFF path byte-identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.packs.context import (
    Capability,
    CapabilityError,
    EvidenceProducerCtx,
    PrimitiveType,
    SessionReadView,
    ValidatorCtx,
    restricted_capabilities_for,
)

_SESSION = SessionReadView(invocation_id="s-1", agent_name="magi", turn_index=0)


# -- (a) default full set never raises ---------------------------------------
def test_default_full_set_never_raises() -> None:
    ValidatorCtx(ref="r", artifact={}, session=_SESSION).emit(passed=True)
    EvidenceProducerCtx(session=_SESSION).emit(evidence_type="t", payload={})


# -- (b) restricted set missing the token raises CapabilityError -------------
def test_restricted_set_missing_token_raises() -> None:
    ctx = ValidatorCtx(ref="r", artifact={}, session=_SESSION,
                       capabilities=frozenset())
    with pytest.raises(CapabilityError):
        ctx.emit(passed=True)


def test_restricted_set_with_token_allows() -> None:
    # A restricted set that DOES carry the token still works (parity proof).
    ctx = ValidatorCtx(
        ref="r", artifact={}, session=_SESSION,
        capabilities=frozenset({Capability.EMIT_VALIDATION}),
    )
    ctx.emit(passed=True)
    assert ctx.verdict() is not None


def test_evidence_restricted_missing_token_raises() -> None:
    ctx = EvidenceProducerCtx(session=_SESSION, capabilities=frozenset())
    with pytest.raises(CapabilityError):
        ctx.emit(evidence_type="t", payload={})


def test_restricted_capabilities_policy() -> None:
    # The documented restricted-set policy per primitive type.
    assert restricted_capabilities_for("tool") == frozenset({Capability.READ_SESSION})
    assert restricted_capabilities_for(PrimitiveType.TOOL) == frozenset(
        {Capability.READ_SESSION}
    )
    assert restricted_capabilities_for("validator") == frozenset(
        {Capability.READ_SESSION, Capability.EMIT_VALIDATION}
    )
    assert restricted_capabilities_for("evidence_producer") == frozenset(
        {Capability.READ_SESSION, Capability.EMIT_EVIDENCE}
    )
    # A validator's restricted set must NOT carry the evidence token (cannot
    # cross-emit into the other primitive's surface).
    assert Capability.EMIT_EVIDENCE not in restricted_capabilities_for("validator")


# ---------------------------------------------------------------------------
# Construction-site wiring (PR2 validator path) through the real engine gate.
# ---------------------------------------------------------------------------
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly  # noqa: E402

_VALIDATOR_REF = "verifier:user-cap-check@1"

_PACK_TOML = """\
packId = "user.user-cap-check-pack"
displayName = "User Cap Check Pack"
version = "0.1.0"
description = "User validator pack for the capability-enforcement test."

[[provides]]
type = "validator"
ref = "verifier:user-cap-check@1"
impl = "user_cap_check_pack.impl:validate"
"""

# This impl overreaches into the TOOL-DECISION surface (DECIDE_TOOL), a token a
# validator never legitimately needs. It tests that token by attempting a sibling
# BeforeToolCtx decide() built from the SAME capability set the engine handed it.
#
# WHEN ENFORCEMENT IS OFF the engine passes ``capabilities=None`` and the test
# patches the ValidatorCtx default to the FULL token set, so the overreach
# succeeds and the impl emits a passing verdict (unblocks). WHEN ON the engine
# passes the restricted ``{READ_SESSION, EMIT_VALIDATION}`` set (no DECIDE_TOOL),
# so the sibling decide() raises CapabilityError; the engine's existing
# try/except converts the raise into a failing verdict and the turn fails closed
# (blocks) rather than crashing. This is the OFF-allows / ON-blocks difference
# the construction-site wiring produces.
_IMPL_PY = '''\
"""User validator impl that overreaches into the tool-decision surface."""
from __future__ import annotations

from magi_agent.packs.context import (
    BeforeToolCtx,
    EvidenceReadView,
    SessionReadView,
    ValidatorCtx,
    ValidatorVerdict,
)

CALLS: list[str] = []


def validate(ctx: ValidatorCtx) -> ValidatorVerdict | None:
    CALLS.append(ctx.ref)
    # Overreach: build a sibling control-plane decision ctx from the SAME
    # capability set the engine handed this validator and try to deny a tool.
    # decide() requires DECIDE_TOOL, which the restricted validator set lacks.
    before = BeforeToolCtx(
        tool_name="x",
        tool_args={},
        session=ctx.session,
        evidence=EvidenceReadView(),
        capabilities=ctx.capabilities,
    )
    before.decide("deny", reason="overreach")  # raises CapabilityError under ON
    ctx.emit(passed=True, detail="overreach succeeded (enforcement OFF)")
    return ctx.verdict()
'''


def _write_pack(packs_base: Path) -> None:
    pack_dir = packs_base / "user_cap_check_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(_PACK_TOML)
    (pack_dir / "impl.py").write_text(_IMPL_PY)


def _driver() -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),
            evidenceRequirements=(),
            requiredValidators=(_VALIDATOR_REF,),
            missingEvidenceAction="audit",
        ),
        evidence_collector=lambda _turn: (),
    )


def _gate(driver: MagiEngineDriver, *, final_text: str) -> dict[str, object]:
    payload = driver._pre_final_gate_payload(
        session_id="s-1",
        turn_id="t-1",
        prompt="please finish the task",
        harness_state=None,
        observed_public_refs=set(),
        coding_mutation_observed=False,
        repair_attempt_count=0,
        final_text=final_text,
        live_selected_pack_ids=(),
    )
    assert payload is not None, "pre-final gate did not apply"
    return payload


def _patch_bases(monkeypatch, packs_base: Path) -> None:
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases",
        lambda: [packs_base],
    )


# -- (c) flag OFF => construction site passes no capabilities= (no restriction) -
def test_enforcement_off_user_validator_unrestricted(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.setenv("MAGI_USER_VALIDATOR_PACKS_ENABLED", "1")
    monkeypatch.delenv("MAGI_PACK_CAPABILITY_ENFORCEMENT_ENABLED", raising=False)

    # OFF: the engine passes ``capabilities=None`` so the ctx carries the
    # ValidatorCtx CLASS DEFAULT. To prove the construction site does NOT
    # restrict when OFF, widen that class default to the full token set for the
    # duration of this test; the overreaching decide() then succeeds and the
    # impl emits a passing verdict (unblocks). (ON, below, ignores the class
    # default and passes the restricted set explicitly.)
    monkeypatch.setattr(
        ValidatorCtx, "capabilities", Capability.all_tokens(), raising=False
    )

    payload = _gate(_driver(), final_text="anything")

    assert payload["decision"] == "pass"
    assert _VALIDATOR_REF in payload["matchedRefs"]


# -- (d) flag ON => overreach is blocked, turn fails closed ------------------
def test_enforcement_on_user_validator_overreach_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.setenv("MAGI_USER_VALIDATOR_PACKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_PACK_CAPABILITY_ENFORCEMENT_ENABLED", "1")

    payload = _gate(_driver(), final_text="anything")

    # ON: the engine hands the impl a restricted {READ_SESSION, EMIT_VALIDATION}
    # set. The impl's sibling evidence emit raises CapabilityError; the engine's
    # try/except converts that to a failing verdict -> the turn BLOCKS (fails
    # closed) instead of crashing.
    assert payload["decision"] == "block"
    assert _VALIDATOR_REF in payload["missingValidators"]
    bus = payload.get("verifierBus")
    assert isinstance(bus, dict)
    verdicts = bus.get("userValidatorVerdicts")
    assert isinstance(verdicts, list) and verdicts
    failing = [v for v in verdicts if v.get("ref") == _VALIDATOR_REF]
    assert failing and failing[0]["passed"] is False
