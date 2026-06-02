from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.transport.tool_preview import MAX_TOOL_PREVIEW, sanitize_tool_preview


def _secret_heavy_preview() -> str:
    return (
        "Authorization: Bearer bearer.SECRET_123~+/=-, "
        "github_primary=ghp_primary_secret_123, "
        "github_oauth=gho_oauth_secret_123, "
        "github_user=ghu_user_secret_123, "
        "github_server=ghs_server_secret_123, "
        "github_refresh=ghr_refresh_secret_123, "
        "openai=sk-secret-token-123, "
        'api_key="api-key-secret", '
        "token='token secret with spaces', "
        "secret=named-secret-value, "
        "password: password-secret-value, "
        f"payload={'x' * 600}"
    )


def test_public_report_projection_redacts_and_truncates_all_preview_fields() -> None:
    from openmagi_core_agent.harness.slop_cleaner import (
        SlopCleanerFinding,
        project_slop_cleaner_public_report,
    )

    raw_preview = _secret_heavy_preview()
    finding = SlopCleanerFinding(
        finding_id="finding-1",
        pattern_id="comment.generic",
        path="src/example.ts",
        line=12,
        severity="warn",
        raw_preview=raw_preview,
    )

    report = project_slop_cleaner_public_report(
        report_id="slop-report-1",
        mode="audit",
        scanned_files=1,
        findings=(finding,),
        requires_reverify=False,
        report_preview=raw_preview,
        artifact_refs=("adk-artifact-1",),
    )

    finding_preview = report.findings[0].public_preview
    assert finding.raw_preview == raw_preview
    assert finding_preview == sanitize_tool_preview(raw_preview)
    assert report.report_preview == sanitize_tool_preview(raw_preview)
    assert len(finding_preview) == MAX_TOOL_PREVIEW
    assert finding_preview.endswith("...")
    assert len(report.report_preview) == MAX_TOOL_PREVIEW
    assert report.report_preview.endswith("...")

    public_text = repr(report.model_dump(by_alias=True, exclude_none=True))
    for secret in (
        "bearer.SECRET_123",
        "ghp_primary_secret_123",
        "gho_oauth_secret_123",
        "ghu_user_secret_123",
        "ghs_server_secret_123",
        "ghr_refresh_secret_123",
        "sk-secret-token-123",
        "api-key-secret",
        "token secret with spaces",
        "named-secret-value",
        "password-secret-value",
    ):
        assert secret not in public_text
    assert "Bearer [redacted]" in public_text
    assert "[redacted]" in public_text
    assert "rawPreview" not in public_text


def test_public_report_accepts_aliases_and_forces_unattached_flags() -> None:
    from openmagi_core_agent.harness.slop_cleaner import SlopCleanerPublicReport

    report = SlopCleanerPublicReport.model_validate(
        {
            "reportId": "slop-report-2",
            "mode": "audit",
            "scannedFiles": 2,
            "findings": [
                {
                    "findingId": "finding-2",
                    "patternId": "boilerplate.decorative",
                    "path": "src/example.py",
                    "line": 8,
                    "severity": "info",
                    "publicPreview": "token=direct-secret",
                    "trafficAttached": False,
                    "executionAttached": False,
                }
            ],
            "changedFiles": [],
            "requiresReverify": False,
            "reportPreview": "api_key=report-secret",
            "artifactRefs": ["artifact-2"],
            "trafficAttached": False,
            "executionAttached": False,
        }
    )

    dumped = report.model_dump(by_alias=True)
    assert dumped["reportId"] == "slop-report-2"
    assert dumped["scannedFiles"] == 2
    assert dumped["requiresReverify"] is False
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["findings"][0]["publicPreview"] == "token=[redacted]"
    assert dumped["reportPreview"] == "api_key=[redacted]"
    assert dumped["artifactRefs"] == ("artifact-2",)

    with pytest.raises(ValidationError):
        SlopCleanerPublicReport.model_validate(
            {
                "reportId": "slop-report-attached",
                "mode": "audit",
                "scannedFiles": 0,
                "findings": [],
                "requiresReverify": False,
                "trafficAttached": True,
            }
        )


@pytest.mark.parametrize("extra_field", ("runnerAttached", "route"))
def test_public_report_rejects_unexpected_runtime_fields(extra_field: str) -> None:
    from openmagi_core_agent.harness.slop_cleaner import SlopCleanerPublicFinding, SlopCleanerPublicReport

    report_payload: dict[str, object] = {
        "reportId": "slop-report-extra",
        "mode": "audit",
        "scannedFiles": 0,
        "findings": [],
        "requiresReverify": False,
        extra_field: False,
    }
    finding_payload: dict[str, object] = {
        "findingId": "finding-extra",
        "patternId": "comment.generic",
        "path": "src/example.ts",
        "severity": "warn",
        extra_field: False,
    }

    with pytest.raises(ValidationError):
        SlopCleanerPublicReport.model_validate(report_payload)

    with pytest.raises(ValidationError):
        SlopCleanerPublicFinding.model_validate(finding_payload)


def test_sse_event_projection_contains_public_shape_only() -> None:
    from openmagi_core_agent.harness.slop_cleaner import (
        SLOP_CLEANER_EXECUTION_ATTACHED,
        SLOP_CLEANER_SSE_EVENT_TYPE,
        SLOP_CLEANER_TRAFFIC_ATTACHED,
        SlopCleanerFinding,
        project_slop_cleaner_public_report,
        slop_cleaner_sse_event,
    )

    raw_preview = "Bearer raw-secret, password=raw-password"
    report = project_slop_cleaner_public_report(
        report_id="slop-report-3",
        mode="audit",
        scanned_files=1,
        findings=(
            SlopCleanerFinding(
                findingId="finding-3",
                patternId="comment.generic",
                path="src/example.go",
                severity="warn",
                rawPreview=raw_preview,
            ),
        ),
        requires_reverify=False,
        report_preview=raw_preview,
    )

    event = slop_cleaner_sse_event(report)

    assert SLOP_CLEANER_TRAFFIC_ATTACHED is False
    assert SLOP_CLEANER_EXECUTION_ATTACHED is False
    assert event["type"] == SLOP_CLEANER_SSE_EVENT_TYPE
    assert event["trafficAttached"] is False
    assert event["executionAttached"] is False
    assert event["report"]["findings"][0]["publicPreview"] == "Bearer [redacted], password=[redacted]"
    assert "raw-secret" not in repr(event)
    assert "raw-password" not in repr(event)
    assert "rawPreview" not in repr(event)


def test_sse_event_revalidates_copied_report_and_finding_before_serializing() -> None:
    from openmagi_core_agent.harness.slop_cleaner import (
        SlopCleanerFinding,
        project_slop_cleaner_public_report,
        slop_cleaner_sse_event,
    )

    raw_preview = "Bearer raw-secret, password=raw-password"
    report = project_slop_cleaner_public_report(
        report_id="slop-report-copy",
        mode="audit",
        scanned_files=1,
        findings=(
            SlopCleanerFinding(
                findingId="finding-copy",
                patternId="comment.generic",
                path="src/example.go",
                severity="warn",
                rawPreview="already sanitized",
            ),
        ),
        requires_reverify=False,
        report_preview="already sanitized",
    )

    copied_finding_with_raw_preview = report.findings[0].model_copy(
        update={"public_preview": raw_preview}
    )
    copied_report_with_raw_preview = report.model_copy(
        update={
            "findings": (copied_finding_with_raw_preview,),
            "report_preview": raw_preview,
        }
    )

    event = slop_cleaner_sse_event(copied_report_with_raw_preview)

    assert event["report"]["findings"][0]["publicPreview"] == (
        "Bearer [redacted], password=[redacted]"
    )
    assert event["report"]["reportPreview"] == "Bearer [redacted], password=[redacted]"
    assert "raw-secret" not in repr(event)
    assert "raw-password" not in repr(event)

    copied_attached_report = report.model_copy(update={"traffic_attached": True})
    with pytest.raises(ValidationError):
        slop_cleaner_sse_event(copied_attached_report)

    copied_attached_finding = report.findings[0].model_copy(
        update={"execution_attached": True}
    )
    copied_report_with_attached_finding = report.model_copy(
        update={"findings": (copied_attached_finding,)}
    )
    with pytest.raises(ValidationError):
        slop_cleaner_sse_event(copied_report_with_attached_finding)


def test_slop_cleaner_import_stays_traffic_free_in_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.harness.slop_cleaner")
forbidden_modules = (
    "google.adk",
    "google.adk.runners",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.hooks.bus",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"slop_cleaner import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
