from __future__ import annotations

import pytest

from magi_agent.harness.coding.code_intelligence_contracts import (
    project_live_diagnostics_report,
)


def _span() -> dict[str, object]:
    return {
        "fileRef": "file-ref:sha256:" + "1" * 64,
        "startLine": 4,
        "startColumn": 2,
        "endLine": 4,
        "endColumn": 17,
        "sourceDigest": "sha256:" + "2" * 64,
    }


def _source_metadata() -> dict[str, object]:
    return {
        "sourceRef": "source-ref:sha256:" + "3" * 64,
        "workspaceRef": "workspace-ref:sha256:" + "4" * 64,
        "languageId": "python",
        "providerRef": "provider-ref:pyright",
    }


def test_disabled_flag_keeps_contract_inert_provider_unavailable() -> None:
    report = project_live_diagnostics_report(
        diagnostics_enabled=False,
        error_count=3,
        span=_span(),
        source_metadata=_source_metadata(),
        result_digest="sha256:" + "5" * 64,
    )
    projection = report.public_projection()
    assert projection["status"] == "provider_unavailable"
    assert projection["success"] is False
    # Authority invariants stay locked off (default-off inert behaviour).
    assert projection["defaultOff"] is True
    assert projection["localOnly"] is True
    assert projection["liveAuthorityAllowed"] is False
    assert projection["coreTouchAllowed"] is False


def test_enabled_flag_projects_live_diagnostics_observation_with_errors() -> None:
    report = project_live_diagnostics_report(
        diagnostics_enabled=True,
        error_count=2,
        span=_span(),
        source_metadata=_source_metadata(),
        result_digest="sha256:" + "9" * 64,
    )
    projection = report.public_projection()
    assert projection["status"] == "projected"
    assert projection["success"] is True
    assert projection["operationStatuses"]["diagnostics"] == "projected"
    assert projection["diagnosticsReportRef"].startswith(
        "artifact:code-intelligence-diagnostics:sha256:"
    )
    # Even when live, the report itself never claims live authority.
    assert projection["liveAuthorityAllowed"] is False
    assert projection["coreTouchAllowed"] is False
    observations = projection["observations"]
    assert len(observations) == 1
    assert observations[0]["operation"] == "diagnostics"
    assert "diagnostics_errors_projected" in observations[0]["reasonCodes"]


def test_enabled_flag_clean_file_still_projects_diagnostics_operation() -> None:
    report = project_live_diagnostics_report(
        diagnostics_enabled=True,
        error_count=0,
        span=_span(),
        source_metadata=_source_metadata(),
        result_digest="sha256:" + "a" * 64,
    )
    projection = report.public_projection()
    assert projection["status"] == "projected"
    assert "diagnostics_clean_projected" in projection["observations"][0]["reasonCodes"]


def test_live_report_rejects_private_paths_in_source_metadata() -> None:
    with pytest.raises(ValueError):
        project_live_diagnostics_report(
            diagnostics_enabled=True,
            error_count=1,
            span=_span(),
            source_metadata={
                **_source_metadata(),
                "providerRef": "/Users/kevin/secret",
            },
            result_digest="sha256:" + "b" * 64,
        )
