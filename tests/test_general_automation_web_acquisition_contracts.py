from __future__ import annotations

from pathlib import Path

import pytest

from openmagi_core_agent.harness.general_automation.web_source_receipts import (
    build_web_source_receipt,
)
from openmagi_core_agent.recipes.first_party.general_automation.web_acquisition_contracts import (
    WebAcquisitionContractRequest,
    classify_web_acquisition_request,
    web_fetch_function_tool_metadata,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = PYTHON_ROOT / "openmagi_core_agent" / "harness" / "general_automation"
RECIPE_DIR = (
    PYTHON_ROOT
    / "openmagi_core_agent"
    / "recipes"
    / "first_party"
    / "general_automation"
)


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


def _fragment(*parts: str) -> str:
    return "".join(parts)


def test_web_fetch_contract_projects_disabled_function_tool_metadata() -> None:
    metadata = web_fetch_function_tool_metadata()

    assert metadata["name"] == "WebFetch"
    assert metadata["adkToolType"] == "FunctionTool"
    assert metadata["enabledByDefault"] is False
    assert metadata["handlerAttached"] is False
    assert metadata["providerCallAttached"] is False
    assert metadata["inputSchema"]["required"] == ["url"]


def test_url_policy_blocks_private_credential_cluster_metadata_and_redirect_targets() -> None:
    blocked_cases = {
        "http://localhost:3000": "local_url_blocked",
        "http://10.0.0.5/admin": "private_url_blocked",
        "http://169.254.169.254/latest/meta-data": "metadata_url_blocked",
        "https://kubernetes.default.svc/api": "cluster_url_blocked",
        "https://docs.example.com/page?token=unsafe": "credential_url_blocked",
    }

    for url, reason in blocked_cases.items():
        decision = classify_web_acquisition_request(WebAcquisitionContractRequest(url=url))
        public = decision.public_projection()

        assert decision.status == "blocked"
        assert reason in decision.reason_codes
        assert public["fetchable"] is False
        assert public["normalizedUrlDigest"].startswith("sha256:")
        assert not _contains_fragment(public, url)

    redirect_decision = classify_web_acquisition_request(
        WebAcquisitionContractRequest(
            url="https://docs.example.com/current",
            redirectTargets=("http://localhost:3000/private",),
        )
    )

    assert redirect_decision.status == "blocked"
    assert redirect_decision.reason_codes == (
        "unsafe_redirect_target_blocked",
        "local_url_blocked",
    )


def test_sensitive_flows_are_blocked_or_approval_required_without_provider_calls() -> None:
    login = classify_web_acquisition_request(
        WebAcquisitionContractRequest(
            url="https://docs.example.com/login",
            flowMarkers=("login",),
        )
    )
    oauth = classify_web_acquisition_request(
        WebAcquisitionContractRequest(
            url="https://docs.example.com/oauth/authorize",
            flowMarkers=("oauth",),
        )
    )
    captcha = classify_web_acquisition_request(
        WebAcquisitionContractRequest(
            url="https://docs.example.com/captcha",
            flowMarkers=("captcha",),
        )
    )
    paywall = classify_web_acquisition_request(
        WebAcquisitionContractRequest(
            url="https://docs.example.com/article",
            flowMarkers=("paywall",),
        )
    )

    assert login.status == "approval_required"
    assert oauth.status == "approval_required"
    assert paywall.status == "approval_required"
    assert captcha.status == "blocked"
    for decision in (login, oauth, captcha, paywall):
        public = decision.public_projection()
        assert public["authorityFlags"] == {
            "providerCalled": False,
            "networkAccessed": False,
            "browserStarted": False,
            "routeAttached": False,
        }


def test_fetchable_url_projection_uses_digests_refs_and_request_dump_hides_url() -> None:
    request = WebAcquisitionContractRequest(
        url="https://docs.example.com/current?utm_source=ad",
        redirectTargets=("https://docs.example.com/final",),
    )
    decision = classify_web_acquisition_request(request)
    public = decision.public_projection()
    dumped = request.model_dump(by_alias=True, mode="json")
    rendered = str(request)

    assert decision.status == "fetchable"
    assert public["fetchable"] is True
    assert public["normalizedUrlDigest"].startswith("sha256:")
    assert public["fetchRequestRef"].startswith("web-fetch:sha256:")
    assert public["adkTool"]["name"] == "WebFetch"
    assert public["authorityFlags"]["networkAccessed"] is False
    assert "docs.example.com" not in str(public)
    assert dumped["url"].startswith("sha256:")
    assert "docs.example.com" not in str(dumped)
    assert "docs.example.com" not in rendered


def test_source_receipt_records_digest_provider_proof_type_and_timestamp_without_raw_url() -> None:
    receipt = build_web_source_receipt(
        url="https://docs.example.com/current?utm_source=ad",
        contentDigest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
        provider="openmagi.webfetch.contract",
        proofType="opened",
        observedAt="2026-05-27T15:00:00Z",
    )
    public = receipt.public_projection()

    assert public["sourceRef"].startswith("source:web:sha256:")
    assert public["evidenceRef"].startswith("evidence:web:sha256:")
    assert public["normalizedUrlDigest"].startswith("sha256:")
    assert public["contentDigest"] == (
        "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    )
    assert public["provider"] == "openmagi.webfetch.contract"
    assert public["proofType"] == "opened"
    assert public["observedAt"] == "2026-05-27T15:00:00Z"
    assert "docs.example.com" not in str(public)
    assert public["adkBoundary"] == {
        "functionTool": "WebFetch",
        "providerCallAttached": False,
    }


def test_source_receipt_rejects_policy_blocked_urls_before_receipt_creation() -> None:
    with pytest.raises(ValueError, match="credential_url_blocked"):
        build_web_source_receipt(
            url="https://docs.example.com/current?token=unsafe",
            contentDigest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            provider="openmagi.webfetch.contract",
            proofType="opened",
            observedAt="2026-05-27T15:00:00Z",
        )


def test_web_acquisition_contract_modules_do_not_touch_core_or_live_surfaces() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            HARNESS_DIR / "web_source_receipts.py",
            RECIPE_DIR / "web_acquisition_contracts.py",
        )
    )

    forbidden_fragments = (
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.runtime",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.tools.registry",
        "openmagi_core_agent.tools.permission",
        "google.adk.runners",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        _fragment("sub", "process"),
        "playwright",
        "selenium",
        _fragment("__", "import", "__("),
        _fragment("import", "lib"),
        ".write_text(",
        ".read_text(",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
