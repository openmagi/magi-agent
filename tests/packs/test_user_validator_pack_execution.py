"""PR2: a user-authored VALIDATOR pack EXECUTES at the pre-final gate, default-OFF.

A user ``validator`` ref already reaches the enforce gate (its static manifest
ref is appended to ``required_validators``), but the validator's own impl was
never executed, so its ref could never be OBSERVED. Enabling a user validator
could therefore ONLY block (never pass). This pins the Phase-3 contract:

- Flag OFF (default): the validator impl is NEVER called and the behavior is
  byte-identical to before: a required-but-unobserved user validator ref blocks.
- Flag ON (``MAGI_USER_VALIDATOR_PACKS_ENABLED=1``) + validator PASSES: its impl
  runs over the produced artifact, emits ``passed=True``, the ref counts as
  observed and the turn is NOT blocked.
- Flag ON + validator FAILS: the impl runs, emits ``passed=False``, the turn is
  blocked AND the failing verdict's detail surfaces in the gate payload.

The user pack authors a validator the documented way: the ``impl`` entry points
directly at a validator callable ``(ValidatorCtx) -> ValidatorVerdict | None``
(identical shape to the bundled first-party ``source_opened_validator``) that
reads ``ctx.artifact`` and calls ``ctx.emit(passed=..., detail=...)``.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly

_VALIDATOR_REF = "verifier:user-final-check@1"

_PACK_TOML = """\
packId = "user.user-final-check-pack"
displayName = "User Final Check Pack"
version = "0.1.0"
description = "User-authored validator pack for the PR2 execution test."

[[provides]]
type = "validator"
ref = "verifier:user-final-check@1"
impl = "user_final_check_pack.impl:validate"
"""

# The impl asserts the produced artifact contains the substring "APPROVED".
_IMPL_PY = '''\
"""User validator impl: read the artifact and emit/return a verdict."""
from __future__ import annotations

from magi_agent.packs.context import ValidatorCtx, ValidatorVerdict

# Module-level call counter so the test can prove the impl never runs when OFF.
CALLS: list[str] = []


def validate(ctx: ValidatorCtx) -> ValidatorVerdict | None:
    CALLS.append(ctx.ref)
    final_text = str(ctx.artifact.get("finalText", ""))
    if "APPROVED" in final_text:
        ctx.emit(passed=True, detail="artifact carries the approval marker")
    else:
        ctx.emit(passed=False, detail="artifact is missing the APPROVED marker")
    return ctx.verdict()
'''


def _write_user_validator_pack(packs_base: Path) -> None:
    pack_dir = packs_base / "user_final_check_pack"
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
            selectedPackIds=("user.quote",),  # non-dev-coding → gate applies
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


def test_user_validator_inert_when_flag_off(tmp_path: Path, monkeypatch) -> None:
    packs_base = tmp_path / "packs"
    _write_user_validator_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.delenv("MAGI_USER_VALIDATOR_PACKS_ENABLED", raising=False)

    payload = _gate(_driver(), final_text="APPROVED: all good")

    # OFF path is byte-identical to before: the required user validator ref is
    # unobserved (its impl never ran), so the turn blocks.
    assert payload["decision"] == "block"
    assert _VALIDATOR_REF in payload["missingValidators"]
    # Prove the impl was never imported/executed.
    import sys

    impl_mod = sys.modules.get("user_final_check_pack.impl")
    if impl_mod is not None:
        assert impl_mod.CALLS == [], "validator impl ran while flag OFF"


def test_user_validator_passes_unblocks_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_user_validator_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.setenv("MAGI_USER_VALIDATOR_PACKS_ENABLED", "1")

    payload = _gate(_driver(), final_text="APPROVED: all good")

    # The validator ran, passed, so its ref is observed → no longer missing.
    assert _VALIDATOR_REF not in payload["missingValidators"]
    assert payload["decision"] == "pass"
    assert _VALIDATOR_REF in payload["matchedRefs"]

    import sys

    impl_mod = sys.modules["user_final_check_pack.impl"]
    assert impl_mod.CALLS == [_VALIDATOR_REF], "validator impl did not run once"


def test_user_validator_fails_blocks_with_detail_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_user_validator_pack(packs_base)
    _patch_bases(monkeypatch, packs_base)
    monkeypatch.setenv("MAGI_USER_VALIDATOR_PACKS_ENABLED", "1")

    payload = _gate(_driver(), final_text="nothing was approved here")

    # The validator ran, failed → ref stays missing → turn blocks.
    assert payload["decision"] == "block"
    assert _VALIDATOR_REF in payload["missingValidators"]

    # The failing verdict detail surfaces on the payload.
    bus = payload.get("verifierBus")
    assert isinstance(bus, dict)
    verdicts = bus.get("userValidatorVerdicts")
    assert isinstance(verdicts, list) and verdicts, "no user validator verdicts surfaced"
    failing = [v for v in verdicts if v.get("ref") == _VALIDATOR_REF]
    assert failing and failing[0]["passed"] is False
    assert "APPROVED" in str(failing[0].get("detail"))
