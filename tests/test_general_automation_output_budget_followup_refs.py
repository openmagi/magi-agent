from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.general_automation.followup_refs import (
    ModeledFollowupRequest,
    validate_followup_ref_request,
)
from magi_agent.harness.general_automation.output_budget_policy import (
    AutomationOutputBudgetRequest,
    apply_output_budget_policy,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"


def _contains_fragment(value: object, fragment: str) -> bool:
    if isinstance(value, str):
        return fragment in value
    if isinstance(value, dict):
        return any(
            _contains_fragment(key, fragment) or _contains_fragment(item, fragment)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_fragment(item, fragment) for item in value)
    return False


def _budget_request(
    *,
    source_kind: str = "shell_log",
    output_text: str = "alpha " * 80,
    preview_chars: int = 48,
) -> AutomationOutputBudgetRequest:
    return AutomationOutputBudgetRequest(
        sourceKind=source_kind,
        outputText=output_text,
        previewChars=preview_chars,
    )


def test_large_outputs_produce_preview_ref_digest_counts_and_followup_tools() -> None:
    decision = apply_output_budget_policy(
        _budget_request(output_text="row-1,value\n" + ("row-2,large-value\n" * 40))
    )

    projection = decision.public_projection()

    assert decision.status == "referenced"
    assert decision.truncated is True
    assert projection["preview"]
    assert projection["fullOutputRef"].startswith("artifact:general-automation-output:sha256:")
    assert projection["digest"].startswith("sha256:")
    assert projection["byteCount"] > len(str(projection["preview"]).encode("utf-8"))
    assert {tool["toolName"] for tool in projection["followupTools"]} == {
        "ReadOutputRef",
        "SearchOutputRef",
    }
    assert projection["adkBoundary"] == {
        "artifactService": "ArtifactService",
        "artifactRef": decision.full_output_ref,
    }


def test_public_projection_redacts_local_paths_and_raw_content() -> None:
    decision = apply_output_budget_policy(
        _budget_request(
            source_kind="browser_dom",
            output_text=(
                "raw_tool_log /Users/acme/private/export.csv "
                "Authorization: Bearer secret-token-value "
                + ("visible-body " * 40)
            ),
            preview_chars=96,
        )
    )

    public = decision.public_projection()

    assert not _contains_fragment(public, "/Users/acme")
    assert not _contains_fragment(public, "raw_tool_log")
    assert not _contains_fragment(public, "secret-token-value")
    assert not _contains_fragment(public, "Authorization")
    assert not _contains_fragment(public, "visible-body " * 20)


def test_budget_request_model_dump_and_repr_do_not_leak_raw_output() -> None:
    request = _budget_request(
        output_text="raw_tool_log /Users/acme/private/export.csv secret-body-marker"
    )

    dumped = request.model_dump(by_alias=True, mode="json")
    rendered = str(request)

    assert dumped["outputText"].startswith("sha256:")
    assert "secret-body-marker" not in str(dumped)
    assert "/Users/acme" not in str(dumped)
    assert "secret-body-marker" not in rendered
    assert "/Users/acme" not in rendered


def test_followup_refs_can_be_consumed_by_modeled_read_and_search_contracts() -> None:
    decision = apply_output_budget_policy(_budget_request(source_kind="pdf_text"))
    ref = decision.followup_ref

    read = validate_followup_ref_request(
        ModeledFollowupRequest(
            operation="read",
            fullOutputRef=ref.full_output_ref,
            digest=ref.digest,
        ),
        available_refs=(ref,),
    )
    search = validate_followup_ref_request(
        ModeledFollowupRequest(
            operation="search",
            fullOutputRef=ref.full_output_ref,
            digest=ref.digest,
            query="invoice",
        ),
        available_refs=(ref,),
    )

    assert read.status == "accepted"
    assert read.operation == "read"
    assert read.content_loaded is False
    assert read.adk_artifact_service_boundary == "ArtifactService"
    assert search.status == "accepted"
    assert search.operation == "search"
    assert search.content_loaded is False


def test_modeled_search_followup_request_requires_query() -> None:
    decision = apply_output_budget_policy(_budget_request(source_kind="mcp_output"))
    ref = decision.followup_ref

    with pytest.raises(ValidationError, match="search follow-up requires query"):
        ModeledFollowupRequest(
            operation="search",
            fullOutputRef=ref.full_output_ref,
            digest=ref.digest,
        )


def test_truncation_never_implies_artifact_delivery_channel_delivery_or_completed_work() -> None:
    decision = apply_output_budget_policy(_budget_request(output_text="x" * 800))
    public = decision.public_projection()

    assert decision.truncated is True
    assert public["authorityFlags"] == {
        "adkArtifactServiceAttached": False,
        "artifactWritten": False,
        "channelDeliveryPerformed": False,
        "completedWorkClaimAllowed": False,
        "userVisibleOutputAllowed": False,
    }
    assert public["deliveryClaimAllowed"] is False
    assert public["completedWorkClaimAllowed"] is False


@pytest.mark.parametrize(
    "source_kind",
    ("browser_dom", "shell_log", "csv_preview", "pdf_text", "mcp_output"),
)
def test_supported_output_kinds_share_the_same_reference_semantics(source_kind: str) -> None:
    decision = apply_output_budget_policy(
        _budget_request(source_kind=source_kind, output_text=f"{source_kind} " * 80)
    )

    public = decision.public_projection()

    assert public["sourceKind"] == source_kind
    assert public["status"] == "referenced"
    assert public["fullOutputRef"].startswith("artifact:general-automation-output:sha256:")
    assert public["digest"].startswith("sha256:")
    assert public["byteCount"] > 0
    assert public["truncated"] is True
    assert "preview" in public
    assert "followupTools" in public
    assert public["adkBoundary"]["artifactService"] == "ArtifactService"


def test_output_budget_followup_modules_do_not_touch_tool_result_or_live_artifact_services() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PACKAGE_DIR / "output_budget_policy.py",
            PACKAGE_DIR / "followup_refs.py",
        )
    )

    forbidden_fragments = (
        "magi_agent.tools.result",
        "ToolResult",
        "google.adk",
        "ArtifactService(",
        "LocalResultStore(",
        "OutputArtifactRegistryBoundary(",
        ".write_text(",
        ".read_text(",
        "open(",
        "subprocess",
        "requests",
        "httpx",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
