from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.gate3a_report import (
    Gate3AAttachmentFlags,
    Gate3AComparisonReport,
    Gate3AParityStatus,
    build_gate3a_comparison_report,
    sanitize_gate3a_public_summary,
)


def _report() -> Gate3AComparisonReport:
    return build_gate3a_comparison_report(
        bundle_id="bundle_local_report_0001",
        shadow_run_id="shadow_local_0001",
        recipe_snapshot_id="recipe_local_research_v1",
        event_projection="pass",
        transcript_projection="pass",
        sse_projection="pass",
    )


def _report_payload(
    *,
    event_projection: Gate3AParityStatus = "pass",
    transcript_projection: Gate3AParityStatus = "pass",
    sse_projection: Gate3AParityStatus = "pass",
    control_projection: Gate3AParityStatus = "not_applicable",
    evidence_audit: Gate3AParityStatus = "audit_only",
    tool_projection: Gate3AParityStatus = "not_applicable",
    public_status: Gate3AParityStatus = "pass",
) -> dict[str, object]:
    return {
        "schemaVersion": "gate3a.comparisonReport.v1",
        "bundleId": "bundle_local_report_0001",
        "shadowRunId": "shadow_local_0001",
        "recipeSnapshotId": "recipe_local_research_v1",
        "sourceRuntime": "typescript-core-agent",
        "shadowRuntime": "python-adk",
        "storageMode": "local_only",
        "attachmentFlags": Gate3AAttachmentFlags(),
        "redaction": {"inputVerified": True, "outputVerified": True, "violations": []},
        "parity": {
            "eventProjection": event_projection,
            "transcriptProjection": transcript_projection,
            "sseProjection": sse_projection,
            "controlProjection": control_projection,
            "evidenceAudit": evidence_audit,
            "toolProjection": tool_projection,
        },
        "failures": [],
        "publicSummary": {
            "status": public_status,
            "preview": "Redacted local replay completed.",
        },
    }


def test_comparison_report_is_immutable_and_revalidates_model_copy() -> None:
    report = _report()

    with pytest.raises(ValidationError):
        report.model_copy(update={"attachmentFlags": {"liveCaptureAttached": True}})

    with pytest.raises(ValidationError):
        report.model_copy(update={"publicSummary": {"status": "pass", "preview": "Bearer abcdefgh"}})


@pytest.mark.parametrize(
    ("parity_field", "status"),
    (
        ("eventProjection", "mismatch"),
        ("transcriptProjection", "missing"),
        ("controlProjection", "extra"),
        ("evidenceAudit", "redaction_violation"),
        ("toolProjection", "runner_failure"),
        ("eventProjection", "invalid_bundle"),
    ),
)
def test_model_validate_rejects_public_pass_status_when_aggregate_parity_fails(
    parity_field: str,
    status: Gate3AParityStatus,
) -> None:
    payload = _report_payload()
    parity = dict(payload["parity"])  # type: ignore[arg-type]
    parity[parity_field] = status
    payload["parity"] = parity

    with pytest.raises(ValidationError):
        Gate3AComparisonReport.model_validate(payload)


@pytest.mark.parametrize(
    ("parity_field", "status"),
    (
        ("eventProjection", "mismatch"),
        ("transcriptProjection", "missing"),
        ("controlProjection", "extra"),
        ("evidenceAudit", "redaction_violation"),
        ("toolProjection", "runner_failure"),
        ("eventProjection", "invalid_bundle"),
    ),
)
def test_model_copy_rejects_public_pass_status_when_aggregate_parity_fails(
    parity_field: str,
    status: Gate3AParityStatus,
) -> None:
    report = _report()
    parity = report.parity.model_dump(by_alias=True, mode="json", warnings=False)
    parity[parity_field] = status

    with pytest.raises(ValidationError):
        report.model_copy(update={"parity": parity})


def test_comparison_report_rejects_model_construct_attachment_flag_tampering() -> None:
    flags = Gate3AAttachmentFlags.model_construct(live_capture_attached=True)

    with pytest.raises(ValidationError):
        Gate3AComparisonReport.model_validate(
            {
                "schemaVersion": "gate3a.comparisonReport.v1",
                "bundleId": "bundle_local_report_0001",
                "shadowRunId": "shadow_local_0001",
                "recipeSnapshotId": "recipe_local_research_v1",
                "sourceRuntime": "typescript-core-agent",
                "shadowRuntime": "python-adk",
                "storageMode": "local_only",
                "attachmentFlags": flags,
                "redaction": {"inputVerified": True, "outputVerified": True, "violations": []},
                "parity": {
                    "eventProjection": "pass",
                    "transcriptProjection": "pass",
                    "sseProjection": "pass",
                    "controlProjection": "not_applicable",
                    "evidenceAudit": "not_applicable",
                    "toolProjection": "not_applicable",
                },
                "failures": [],
                "publicSummary": {"status": "pass", "preview": "Redacted local replay completed."},
            }
        )


def test_all_attachment_flags_are_forced_false_in_public_output() -> None:
    dumped = _report().model_dump(by_alias=True)

    assert dumped["attachmentFlags"] == {
        "liveCaptureAttached": False,
        "productionRouteAttached": False,
        "productionStorageAttached": False,
        "userVisibleOutputAttached": False,
        "telegramAttached": False,
        "toolSideEffectsAttached": False,
        "evidenceBlockModeAttached": False,
    }


def test_constructed_report_dump_forces_safe_public_boundary() -> None:
    constructed = Gate3AComparisonReport.model_construct(
        bundle_id="bundle_local_report_0001",
        shadow_run_id="shadow_local_0001",
        recipe_snapshot_id="recipe_local_research_v1",
        storage_mode="remote_prod",
        custom_runtime_loop=True,
        attachment_flags=Gate3AAttachmentFlags.model_construct(telegram_attached=True),
        redaction={"inputVerified": True, "outputVerified": True, "violations": []},
        parity={
            "eventProjection": "pass",
            "transcriptProjection": "pass",
            "sseProjection": "pass",
            "evidenceAudit": "not_applicable",
        },
        failures=("Bearer abcdefghijklmnop /data/bots/bot-123/workspace",),
        public_summary={
            "status": "pass",
            "preview": "Bearer abcdefghijklmnop /data/bots/bot-123/workspace",
        },
    )

    dumped = constructed.model_dump(by_alias=True, mode="json", warnings=False)
    dumped_text = str(dumped)

    assert dumped["storageMode"] == "local_only"
    assert dumped["customRuntimeLoop"] is False
    assert dumped["attachmentFlags"]["telegramAttached"] is False
    assert dumped["parity"]["evidenceAudit"] == "audit_only"
    assert "Bearer" not in dumped_text
    assert "abcdefghijklmnop" not in dumped_text
    assert "/data/bots/bot-123/workspace" not in dumped_text


def test_constructed_report_dump_sanitizes_ids_and_redaction_violations() -> None:
    constructed = Gate3AComparisonReport.model_construct(
        bundle_id="bundle /Users/kevin/Desktop/clawy/secret.txt sk-abcdefghijklmnop",
        shadow_run_id="shadow C:\\Users\\kevin\\secret.txt",
        recipe_snapshot_id="recipe_local_research_v1",
        redaction={
            "inputVerified": True,
            "outputVerified": True,
            "violations": (
                "/Users/kevin/Desktop/clawy/private.txt",
                "Authorization: Bearer abcdefghijklmnop",
                "GITHUB_TOKEN='custom-token-value'",
                'STRIPE_SECRET_KEY="custom-secret-value"',
            ),
        },
        parity={
            "eventProjection": "pass",
            "transcriptProjection": "pass",
            "sseProjection": "pass",
        },
        public_summary={"status": "pass", "preview": "Redacted local replay completed."},
    )

    dumped = constructed.model_dump(by_alias=True, mode="json", warnings=False)
    dumped_text = str(dumped)

    assert "/Users/kevin" not in dumped_text
    assert "C:\\Users\\kevin" not in dumped_text
    assert "sk-abcdefghijklmnop" not in dumped_text
    assert "Bearer" not in dumped_text
    assert "custom-token-value" not in dumped_text
    assert "custom-secret-value" not in dumped_text
    assert "GITHUB_TOKEN" not in dumped_text
    assert "STRIPE_SECRET_KEY" not in dumped_text
    assert dumped["redaction"]["inputVerified"] is True
    assert dumped["redaction"]["outputVerified"] is True


def test_public_summary_is_redacted_and_truncated() -> None:
    summary = sanitize_gate3a_public_summary(
        "Authorization: Bearer abcdefghijklmnop "
        + ("This diagnostic local replay summary is intentionally long. " * 20),
        max_chars=96,
    )

    assert "Bearer" not in summary
    assert "abcdefghijklmnop" not in summary
    assert len(summary) <= 96


def test_public_summary_redacts_quoted_env_credential_assignments() -> None:
    summary = sanitize_gate3a_public_summary(
        "GITHUB_TOKEN='custom-token-value' STRIPE_SECRET_KEY=\"custom-secret-value\"",
        max_chars=180,
    )

    assert "custom-token-value" not in summary
    assert "custom-secret-value" not in summary
    assert "GITHUB_TOKEN" not in summary
    assert "STRIPE_SECRET_KEY" not in summary


def test_model_validate_sanitizes_public_summary_preview_in_memory() -> None:
    payload = _report_payload()
    payload["publicSummary"] = {
        "status": "pass",
        "preview": "GITHUB_TOKEN='custom-token-value'",
    }

    report = Gate3AComparisonReport.model_validate(payload)

    assert "GITHUB_TOKEN" not in report.public_summary.preview
    assert "custom-token-value" not in report.public_summary.preview


def test_model_validate_sanitizes_redaction_violations_in_memory() -> None:
    payload = _report_payload()
    payload["redaction"] = {
        "inputVerified": True,
        "outputVerified": True,
        "violations": (
            "GITHUB_TOKEN='custom-token-value'",
            'STRIPE_SECRET_KEY="custom-secret-value"',
        ),
    }

    report = Gate3AComparisonReport.model_validate(payload)
    violations_text = str(report.redaction.violations)

    assert "GITHUB_TOKEN" not in violations_text
    assert "STRIPE_SECRET_KEY" not in violations_text
    assert "custom-token-value" not in violations_text
    assert "custom-secret-value" not in violations_text


def test_model_validate_sanitizes_failures_in_memory() -> None:
    payload = _report_payload()
    payload["failures"] = (
        "GITHUB_TOKEN='custom-token-value'",
        'STRIPE_SECRET_KEY="custom-secret-value"',
    )

    report = Gate3AComparisonReport.model_validate(payload)
    failures_text = str(report.failures)

    assert "GITHUB_TOKEN" not in failures_text
    assert "STRIPE_SECRET_KEY" not in failures_text
    assert "custom-token-value" not in failures_text
    assert "custom-secret-value" not in failures_text


def test_model_copy_sanitizes_quoted_env_credentials_in_memory() -> None:
    report = _report().model_copy(
        update={
            "publicSummary": {
                "status": "pass",
                "preview": "GITHUB_TOKEN='custom-token-value'",
            },
            "failures": ('STRIPE_SECRET_KEY="custom-secret-value"',),
        }
    )
    model_text = str((report.public_summary.preview, report.failures))

    assert "GITHUB_TOKEN" not in model_text
    assert "STRIPE_SECRET_KEY" not in model_text
    assert "custom-token-value" not in model_text
    assert "custom-secret-value" not in model_text


@pytest.mark.parametrize(
    ("event_projection", "transcript_projection", "control_projection", "evidence_audit", "tool_projection"),
    (
        ("mismatch", "pass", "not_applicable", "audit_only", "not_applicable"),
        ("pass", "mismatch", "not_applicable", "audit_only", "not_applicable"),
        ("pass", "pass", "mismatch", "audit_only", "not_applicable"),
        ("pass", "pass", "not_applicable", "mismatch", "not_applicable"),
        ("pass", "pass", "not_applicable", "audit_only", "missing"),
    ),
)
def test_public_summary_status_reflects_aggregate_comparable_parity(
    event_projection: Gate3AParityStatus,
    transcript_projection: Gate3AParityStatus,
    control_projection: Gate3AParityStatus,
    evidence_audit: Gate3AParityStatus,
    tool_projection: Gate3AParityStatus,
) -> None:
    report = build_gate3a_comparison_report(
        bundle_id="bundle_local_report_0001",
        shadow_run_id="shadow_local_0001",
        recipe_snapshot_id="recipe_local_research_v1",
        event_projection=event_projection,
        transcript_projection=transcript_projection,
        sse_projection="not_applicable",
        control_projection=control_projection,
        evidence_audit=evidence_audit,
        tool_projection=tool_projection,
    )

    assert report.public_summary.status != "pass"


def test_public_report_rejects_raw_secret_path_patterns() -> None:
    with pytest.raises(ValidationError):
        build_gate3a_comparison_report(
            bundle_id="bundle_local_report_0001",
            shadow_run_id="shadow_local_0001",
            recipe_snapshot_id="recipe_local_research_v1",
            event_projection="redaction_violation",
            transcript_projection="redaction_violation",
            sse_projection="not_applicable",
            public_preview="/data/bots/bot-123/workspace Authorization: Bearer abcdefgh",
        )


def test_parity_categories_are_closed_set() -> None:
    assert set(Gate3AParityStatus.__args__) == {
        "pass",
        "mismatch",
        "missing",
        "extra",
        "redaction_violation",
        "runner_failure",
        "invalid_bundle",
        "audit_only",
        "not_applicable",
    }

    report = build_gate3a_comparison_report(
        bundle_id="bundle_local_report_0001",
        shadow_run_id="shadow_local_0001",
        recipe_snapshot_id="recipe_local_research_v1",
        event_projection="pass",
        transcript_projection="pass",
        sse_projection="pass",
    )

    assert report.parity.evidence_audit == "audit_only"


def test_report_validation_errors_hide_raw_input_values() -> None:
    raw_path = "/data/bots/bot-123/workspace"

    with pytest.raises(ValidationError) as exc_info:
        build_gate3a_comparison_report(
            bundle_id="bundle_local_report_0001",
            shadow_run_id="shadow_local_0001",
            recipe_snapshot_id="recipe_local_research_v1",
            event_projection="redaction_violation",
            transcript_projection="redaction_violation",
            sse_projection="not_applicable",
            public_preview=raw_path,
        )

    assert raw_path not in str(exc_info.value)
