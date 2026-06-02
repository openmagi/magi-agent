from __future__ import annotations

from pathlib import Path

from magi_agent.harness.general_automation.browser_evidence import (
    build_browser_artifact_evidence,
    evaluate_browser_side_effect,
)
from magi_agent.recipes.first_party.general_automation.browser_contracts import (
    BrowserBoundaryRequest,
    browser_action_function_tool_metadata,
    classify_browser_boundary_request,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"
RECIPE_DIR = (
    PYTHON_ROOT
    / "magi_agent"
    / "recipes"
    / "first_party"
    / "general_automation"
)


def _fragment(*parts: str) -> str:
    return "".join(parts)


def test_browser_function_tool_metadata_is_disabled_and_worker_free() -> None:
    metadata = browser_action_function_tool_metadata()

    assert metadata["adkToolType"] == "FunctionTool"
    assert metadata["enabledByDefault"] is False
    assert metadata["handlerAttached"] is False
    assert metadata["browserWorkerSessionStarted"] is False
    assert set(metadata["inputSchema"]["properties"]["action"]["enum"]) >= {
        "open",
        "snapshot",
        "scrape",
        "click",
        "fill",
        "download",
        "submit",
    }


def test_inspect_mode_allows_only_open_snapshot_and_scrape_metadata() -> None:
    allowed = [
        classify_browser_boundary_request(
            BrowserBoundaryRequest(mode="inspect", action=action)
        )
        for action in ("open", "snapshot", "scrape")
    ]
    blocked = classify_browser_boundary_request(
        BrowserBoundaryRequest(mode="inspect", action="click")
    )

    assert [decision.status for decision in allowed] == ["allowed", "allowed", "allowed"]
    assert blocked.status == "blocked"
    assert blocked.reason_codes == ("inspect_mode_action_not_allowed",)
    for decision in (*allowed, blocked):
        assert decision.public_projection()["authorityFlags"] == {
            "browserWorkerSessionStarted": False,
            "browserActionPerformed": False,
            "externalFormSubmitted": False,
            "channelDeliveryPerformed": False,
            "routeAttached": False,
        }


def test_act_mode_requires_approval_for_click_fill_download_and_submit() -> None:
    for action in ("click", "fill", "download", "submit"):
        pending = classify_browser_boundary_request(
            BrowserBoundaryRequest(mode="act", action=action)
        )
        approved = classify_browser_boundary_request(
            BrowserBoundaryRequest(
                mode="act",
                action=action,
                approvalRef="approval:browser-action:sha256:"
                "1111111111111111111111111111111111111111111111111111111111111111",
            )
        )

        assert pending.status == "approval_required"
        assert pending.reason_codes == ("browser_action_requires_approval",)
        assert approved.status == "approved"
        assert approved.approval_ref is not None
        assert approved.public_projection()["authorityFlags"]["browserActionPerformed"] is False


def test_browser_artifacts_are_digest_ref_based_without_private_payloads() -> None:
    screenshot = build_browser_artifact_evidence(
        kind="screenshot",
        contentDigest="sha256:2222222222222222222222222222222222222222222222222222222222222222",
        sourceRef="source:browser:sha256:"
        "3333333333333333333333333333333333333333333333333333333333333333",
        label="account page /Users/acme/private visible text",
    )
    dom = build_browser_artifact_evidence(
        kind="dom_summary",
        contentDigest="sha256:4444444444444444444444444444444444444444444444444444444444444444",
        sourceRef="source:browser:sha256:"
        "3333333333333333333333333333333333333333333333333333333333333333",
        label="private markup Cookie: session=unsafe",
    )
    download = build_browser_artifact_evidence(
        kind="download",
        contentDigest="sha256:5555555555555555555555555555555555555555555555555555555555555555",
        sourceRef="source:browser:sha256:"
        "3333333333333333333333333333333333333333333333333333333333333333",
        label="report.csv",
    )
    action_receipt = build_browser_artifact_evidence(
        kind="action_receipt",
        contentDigest="sha256:9999999999999999999999999999999999999999999999999999999999999999",
        sourceRef="source:browser:sha256:"
        "3333333333333333333333333333333333333333333333333333333333333333",
        label="clicked submit near private account selector",
    )

    for receipt in (screenshot, dom, download, action_receipt):
        public = receipt.public_projection()
        assert public["artifactRef"].startswith("artifact:browser-")
        assert public["contentDigest"].startswith("sha256:")
        assert public["sourceRef"].startswith("source:browser:sha256:")
        assert public["adkBoundary"]["artifactService"] == "ArtifactService"
        assert "/Users/acme" not in str(public)
        assert "Cookie:" not in str(public)
        assert "session=unsafe" not in str(public)


def test_external_submission_and_channel_delivery_are_separate_approval_gates() -> None:
    artifact_ref = (
        "artifact:browser-action-receipt:sha256:"
        "6666666666666666666666666666666666666666666666666666666666666666"
    )

    submission_pending = evaluate_browser_side_effect(
        sideEffect="external_form_submission",
        artifactRef=artifact_ref,
    )
    submission_approved = evaluate_browser_side_effect(
        sideEffect="external_form_submission",
        artifactRef=artifact_ref,
        approvalRef="approval:browser-submit:sha256:"
        "7777777777777777777777777777777777777777777777777777777777777777",
    )
    delivery_pending = evaluate_browser_side_effect(
        sideEffect="channel_delivery",
        artifactRef=artifact_ref,
    )
    delivery_recorded = evaluate_browser_side_effect(
        sideEffect="channel_delivery",
        artifactRef=artifact_ref,
        channelDeliveryReceiptRef="receipt:channel:sha256:"
        "8888888888888888888888888888888888888888888888888888888888888888",
    )

    assert submission_pending.status == "approval_required"
    assert submission_approved.status == "approved"
    assert submission_approved.public_projection()["authorityFlags"][
        "externalFormSubmitted"
    ] is False
    assert delivery_pending.status == "approval_required"
    assert delivery_recorded.status == "recorded"
    assert delivery_recorded.public_projection()["authorityFlags"][
        "channelDeliveryPerformed"
    ] is False


def test_browser_contract_modules_do_not_touch_core_or_live_surfaces() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            HARNESS_DIR / "browser_evidence.py",
            RECIPE_DIR / "browser_contracts.py",
        )
    )

    forbidden_fragments = (
        "magi_agent.adk_bridge",
        "magi_agent.runtime",
        "magi_agent.transport",
        "magi_agent.tools.dispatcher",
        "magi_agent.tools.registry",
        "magi_agent.tools.permission",
        "google.adk.runners",
        "BrowserProviderPack(",
        "LocalBrowserProviderRuntime(",
        "playwright",
        "selenium",
        "cdp",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        _fragment("sub", "process"),
        _fragment("__", "import", "__("),
        _fragment("import", "lib"),
        ".write_text(",
        ".read_text(",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
