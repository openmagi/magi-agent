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


# ---------------------------------------------------------------------------
# Phase 0 lab fix: only upgrade audit→repair_required for coding-scope profiles.
# A non-coding task profile (e.g. ``chat`` only) keeps the conservative audit
# posture so a missing-evidence verdict on a chat turn never escalates to a
# hard block that would trigger the repair-loop preamble.
# ---------------------------------------------------------------------------


def test_coding_task_profile_upgrades_audit_to_repair_required() -> None:
    assert (
        _local_trust_missing_evidence_action(
            "audit",
            env={},
            task_profile={"taskTypes": ["coding"]},
        )
        == "repair_required"
    )


def test_non_coding_task_profile_keeps_audit() -> None:
    assert (
        _local_trust_missing_evidence_action(
            "audit",
            env={},
            task_profile={"taskTypes": ["chat"]},
        )
        == "audit"
    )


def test_mixed_profile_with_coding_signal_upgrades() -> None:
    assert (
        _local_trust_missing_evidence_action(
            "audit",
            env={},
            task_profile={"taskTypes": ["chat", "coding"]},
        )
        == "repair_required"
    )


def test_missing_task_profile_preserves_historic_behaviour() -> None:
    # Backwards-compat: callers that do not pass ``task_profile`` still see the
    # historic upgrade so other call sites (and the existing tests above) keep
    # their meaning.
    assert _local_trust_missing_evidence_action("audit", env={}) == "repair_required"
