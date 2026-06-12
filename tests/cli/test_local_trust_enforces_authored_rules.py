"""Task 6.5: local full-trust enforces authored rules by default.

Hosted runs stage authority — a missing authored evidence requirement only
*audits*. For OSS local full-trust the author IS the operator, so a missing
requirement should enforce (``repair_required``) by default. Safe/eval/minimal
profiles (the same set that gates every other full-runtime feature) keep the
conservative hosted ``audit`` posture. An explicit ``repair_required`` is never
downgraded.
"""
from __future__ import annotations

from magi_agent.cli.real_runner import _local_trust_missing_evidence_action


def test_local_full_trust_upgrades_audit_to_repair_required() -> None:
    assert (
        _local_trust_missing_evidence_action("audit", env={})
        == "repair_required"
    )


def test_safe_profile_keeps_hosted_audit_posture() -> None:
    assert (
        _local_trust_missing_evidence_action(
            "audit", env={"MAGI_RUNTIME_PROFILE": "safe"}
        )
        == "audit"
    )


def test_eval_profile_keeps_hosted_audit_posture() -> None:
    assert (
        _local_trust_missing_evidence_action(
            "audit", env={"MAGI_RUNTIME_PROFILE": "eval"}
        )
        == "audit"
    )


def test_explicit_repair_required_is_preserved() -> None:
    assert (
        _local_trust_missing_evidence_action("repair_required", env={})
        == "repair_required"
    )


def test_non_audit_non_repair_action_is_passed_through() -> None:
    # An action that is neither "audit" nor "repair_required" (e.g. "block") is
    # not upgraded — only the conservative hosted "audit" default is flipped.
    assert _local_trust_missing_evidence_action("block", env={}) == "block"
