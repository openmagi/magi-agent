from __future__ import annotations

import ast
import json
from pathlib import Path


def test_acquisition_plan_is_default_off_and_never_executes_provider_or_browser() -> None:
    from magi_agent.web_acquisition.acquisition_plan import (
        WebAcquisitionPlanRequest,
        build_web_acquisition_plan,
    )

    plan = build_web_acquisition_plan(
        WebAcquisitionPlanRequest(
            turnId="turn-plan",
            query="current docs",
            url="https://docs.example.com/current",
            allowBrowserFallback=True,
        )
    )
    dumped = plan.model_dump(by_alias=True)

    assert plan.status == "disabled"
    assert [phase.phase for phase in plan.phases] == [
        "web_search",
        "fetch",
        "reader_extract",
        "metadata_jsonld",
        "browser_snapshot_fallback",
    ]
    assert all(phase.execution_allowed is False for phase in plan.phases)
    assert dumped["attachmentFlags"] == {
        "networkFetched": False,
        "browserExecuted": False,
        "liveProviderCalled": False,
        "rawContentInjected": False,
        "parentContextInjected": False,
        "productionAuthority": False,
    }
    assert dumped["fallbackIntent"]["approvalStatus"] == "required"


def test_acquisition_plan_orders_phases_and_records_retry_budget_quality_and_source_refs() -> None:
    from magi_agent.web_acquisition.acquisition_plan import (
        WebAcquisitionPlanConfig,
        WebAcquisitionPlanRequest,
        build_web_acquisition_plan,
    )

    plan = build_web_acquisition_plan(
        WebAcquisitionPlanRequest(
            turnId="turn-plan",
            query="  current   source ",
            url="https://docs.example.com/current?utm=1",
            observedQualityScore=0.42,
            allowBrowserFallback=True,
            approvalGranted=True,
        ),
        config=WebAcquisitionPlanConfig(enabled=True, maxPhaseAttempts=2, minQualityScore=0.75),
    )
    encoded = json.dumps(plan.model_dump(by_alias=True), sort_keys=True)

    assert plan.status == "planned"
    assert [phase.phase for phase in plan.phases] == [
        "web_search",
        "fetch",
        "reader_extract",
        "metadata_jsonld",
        "browser_snapshot_fallback",
    ]
    assert [phase.provider_capability for phase in plan.phases] == [
        "search_api",
        "fetch",
        "reader_extraction",
        "metadata_jsonld",
        "browser_snapshot",
    ]
    assert all(phase.max_attempts == 2 for phase in plan.phases)
    assert plan.quality_decision.status == "fallback_recommended"
    assert plan.fallback_intent is not None
    assert plan.fallback_intent.approval_status == "approved"
    assert plan.source_ledger_records[0].source_ref == "source:web:plan-1"
    assert plan.source_ledger_records[0].evidence_ref == "evidence:web:plan-1"
    assert plan.source_ledger_records[0].url_ref.startswith("url:")
    assert "docs.example.com/current?utm=1" not in encoded
    assert "networkFetched" in encoded


def test_acquisition_plan_blocks_private_urls_and_does_not_emit_raw_url_context() -> None:
    from magi_agent.web_acquisition.acquisition_plan import (
        WebAcquisitionPlanConfig,
        WebAcquisitionPlanRequest,
        build_web_acquisition_plan,
    )

    plan = build_web_acquisition_plan(
        WebAcquisitionPlanRequest(
            turnId="turn-private",
            query="metadata",
            url="https://user:pass@example.com/private?token=unsafe",
            allowBrowserFallback=True,
            approvalGranted=True,
        ),
        config=WebAcquisitionPlanConfig(enabled=True),
    )
    encoded = json.dumps(plan.model_dump(by_alias=True), sort_keys=True)

    assert plan.status == "blocked"
    assert "credential_url_blocked" in plan.diagnostic_metadata["reasonCodes"]
    assert plan.fallback_intent is None
    assert "user:pass" not in encoded
    assert "token=unsafe" not in encoded
    assert "example.com/private" not in encoded


def test_acquisition_plan_blocks_blank_query_without_source_records() -> None:
    from magi_agent.web_acquisition.acquisition_plan import (
        WebAcquisitionPlanConfig,
        WebAcquisitionPlanRequest,
        build_web_acquisition_plan,
    )

    plan = build_web_acquisition_plan(
        WebAcquisitionPlanRequest(turnId="turn-blank", query="   "),
        config=WebAcquisitionPlanConfig(enabled=True),
    )

    assert plan.status == "blocked"
    assert plan.source_ledger_records == ()
    assert plan.fallback_intent is None
    assert "query_required" in plan.diagnostic_metadata["reasonCodes"]
    assert all(phase.status == "blocked" for phase in plan.phases)


def test_acquisition_plan_redacts_provider_tokens_from_query_diagnostics() -> None:
    from magi_agent.web_acquisition.acquisition_plan import (
        WebAcquisitionPlanConfig,
        WebAcquisitionPlanRequest,
        build_web_acquisition_plan,
    )

    plan = build_web_acquisition_plan(
        WebAcquisitionPlanRequest(
            query=(
                "public docs github_pat_unsafeToken12345 "
                "xoxb-unsafeToken12345 AKIAUNSAFEKEY12345 "
                "AIzaUnsafeGoogleToken12345 /home/kevin/.ssh/id_rsa "
                "/var/lib/kubelet/pods/x"
            ),
        ),
        config=WebAcquisitionPlanConfig(enabled=True),
    )
    encoded = json.dumps(plan.model_dump(by_alias=True), sort_keys=True)

    assert "public docs" in encoded
    for forbidden in (
        "github_pat_unsafe",
        "xoxb-unsafe",
        "AKIAUNSAFE",
        "AIzaUnsafe",
        "/home/kevin",
        "/var/lib/kubelet",
    ):
        assert forbidden not in encoded


def test_acquisition_plan_drops_key_named_metadata_credentials() -> None:
    from magi_agent.web_acquisition.acquisition_plan import (
        WebAcquisitionPlanConfig,
        WebAcquisitionPlanRequest,
        build_web_acquisition_plan,
    )

    plan = build_web_acquisition_plan(
        WebAcquisitionPlanRequest(
            query="public docs",
            metadata={
                "apiKey": "plain-provider-credential",
                "privateKey": "plain-private-key",
                "serviceKey": "plain-service-key",
                "credentialId": "plain-credential-id",
                "authorizationHeader": "plain-auth-header",
                "safeNote": "safe",
            },
        ),
        config=WebAcquisitionPlanConfig(enabled=True),
    )
    encoded = json.dumps(plan.model_dump(by_alias=True), sort_keys=True)

    assert "plain-provider-credential" not in encoded
    assert "plain-private-key" not in encoded
    assert "plain-service-key" not in encoded
    assert "plain-credential-id" not in encoded
    assert "plain-auth-header" not in encoded
    assert "safe" in encoded


def test_acquisition_plan_import_boundary_has_no_live_provider_runtime_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "web_acquisition"
        / "acquisition_plan.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "google.adk",
        "magi_agent.adk_bridge",
        "magi_agent.tools",
        "magi_agent.transport",
        "socket",
        "subprocess",
        "httpx",
        "requests",
        "aiohttp",
        "selenium",
        "playwright",
    )

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    for fragment in ("__import__(", "importlib.import_module", "requests.get", "httpx."):
        assert fragment not in source
