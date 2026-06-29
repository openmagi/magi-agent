"""WS6 PR6b pairing invariant: verification flag never in effect without hedge.

Design: WS6 deterministic-verification activation, PR6b. The fact-grounding
verification flag is already seeded "1" under the lab profile; without the hedge
flag the existing hard ``pre_final_evidence_gate_blocked`` refuse is
user-visible. The lab profile resolution (and a standalone guard) must ensure
the hedge flag is in effect whenever the verification flag is.
"""
from __future__ import annotations

from magi_agent.config.env import (
    parse_evidence_hedge_on_guess_enabled,
    parse_fact_grounding_verification_enabled,
)
from magi_agent.runtime.local_defaults import (
    apply_evidence_hedge_pairing_guard,
    apply_lab_runtime_defaults,
)

_VERIFY = "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED"
_HEDGE = "MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED"


def test_lab_profile_pairs_verification_with_hedge() -> None:
    # The lab profile resolution sets the hedge flag whenever the verification
    # flag is set, so the hard-refuse path cannot be reached under lab.
    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)

    assert parse_fact_grounding_verification_enabled(env) is True
    assert parse_evidence_hedge_on_guess_enabled(env) is True


def test_verification_without_hedge_flag_is_guarded() -> None:
    # Standalone misconfig: the verification flag set by hand without the hedge
    # flag is caught by the startup guard (the hedge flag is paired on).
    env = {_VERIFY: "1"}
    apply_evidence_hedge_pairing_guard(env)

    assert env[_HEDGE] == "1"
    assert parse_evidence_hedge_on_guess_enabled(env) is True


def test_guard_no_op_when_verification_off() -> None:
    # The guard never activates the hedge flag when the verification flag is off
    # (a fresh install / hosted serve is untouched).
    env: dict[str, str] = {}
    apply_evidence_hedge_pairing_guard(env)

    assert _HEDGE not in env


def test_guard_honors_explicit_hedge_opt_out() -> None:
    # An explicit MAGI_EVIDENCE_HEDGE_ON_GUESS_ENABLED=0 still wins (setdefault).
    env = {_VERIFY: "1", _HEDGE: "0"}
    apply_evidence_hedge_pairing_guard(env)

    assert env[_HEDGE] == "0"
    assert parse_evidence_hedge_on_guess_enabled(env) is False
