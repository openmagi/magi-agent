from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError


class FakeOpenCodeWebProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return {
            "results": [
                {
                    "title": "OpenCode Docs",
                    "url": "https://docs.example.com/opencode?utm_source=test",
                    "snippet": "Fixture search result content.",
                    "metadata": {
                        "quality": 0.92,
                        "providerLog": "raw provider log",
                    },
                }
            ],
            "preview": "Search preview with Cookie: sid=unsafe.",
        }

    async def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return {
            "url": "https://docs.example.com/opencode",
            "title": "OpenCode Docs",
            "content": (
                "Fixture fetched content.\n"
                "raw_tool_log: Cookie: sid=unsafe\n"
                "/Users/kevin/private/source.txt"
            ),
            "metadata": {"status": 200, "contentType": "text/html"},
        }


def test_opencode_web_router_is_default_off_without_provider_route() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        build_opencode_web_research_tool_boundary,
        materialize_opencode_web_provider_router,
    )

    decision = materialize_opencode_web_provider_router(profile_key="scout_web_docs")

    assert decision.status == "disabled"
    assert decision.reason_codes == ("rollout_gate_disabled",)
    assert decision.tool_names == ()
    assert decision.web_acquisition_config.enabled is False
    assert decision.web_acquisition_config.local_fake_provider_enabled is False
    assert decision.default_off is True
    assert decision.local_only is True
    assert decision.fixture_only is True
    assert decision.fake_provider_only is True
    assert decision.live_authority_allowed is False
    assert decision.local_fake_provider_route_allowed is False
    assert decision.toolhost_execution_allowed is False
    assert decision.model_calls_allowed is False
    assert set(decision.attachment_flags.values()) == {False}
    assert build_opencode_web_research_tool_boundary(decision, provider_handle=None) is None


def test_opencode_web_router_fails_closed_without_fake_provider_boundary() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        materialize_opencode_web_provider_router,
    )

    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=False,
        local_fake_provider_available=True,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("fake_provider_boundary_disabled",)
    assert decision.tool_names == ()
    assert decision.local_fake_provider_route_allowed is False
    assert decision.web_acquisition_config.enabled is False
    assert decision.live_authority_allowed is False
    assert set(decision.attachment_flags.values()) == {False}


def test_opencode_web_router_does_not_route_repo_or_external_profiles() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        materialize_opencode_web_provider_router,
    )

    repo = materialize_opencode_web_provider_router(
        profile_key="scout_repo_fixture",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    external = materialize_opencode_web_provider_router(
        profile_key="scout_external_repo",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )

    assert repo.status == "blocked"
    assert repo.reason_codes == ("profile_has_no_web_tools",)
    assert repo.tool_names == ()
    assert external.status == "blocked"
    assert external.reason_codes == ("live_network_not_allowed",)
    assert external.tool_names == ()


def test_opencode_web_router_ready_builds_fake_provider_boundary_only() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OPENCODE_WEB_FAKE_PROVIDER_ID,
        OpenCodeLocalFixtureWebProvider,
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    provider = OpenCodeLocalFixtureWebProvider(
        search_payload={
            "results": [
                {
                    "title": "OpenCode Docs",
                    "url": "https://docs.example.com/opencode?utm_source=test",
                    "snippet": "Fixture search result content.",
                    "metadata": {
                        "quality": 0.92,
                        "providerLog": "raw provider log",
                    },
                }
            ],
            "preview": "Search preview with Cookie: sid=unsafe.",
        },
        fetch_payload={
            "url": "https://docs.example.com/opencode",
            "title": "OpenCode Docs",
            "content": (
                "Fixture fetched content.\n"
                "raw_tool_log: Cookie: sid=unsafe\n"
                "/Users/kevin/private/source.txt"
            ),
            "metadata": {"status": 200, "contentType": "text/html"},
        },
    )
    provider_handle = issue_opencode_fixture_provider_handle(provider)
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=provider_handle,
    )

    assert decision.status == "ready"
    assert decision.reason_codes == ("fake_provider_route_ready",)
    assert decision.tool_names == ("FixtureWebSearch", "FixtureWebFetch")
    assert decision.provider_id == OPENCODE_WEB_FAKE_PROVIDER_ID
    assert decision.web_acquisition_config.enabled is True
    assert decision.web_acquisition_config.local_fake_provider_enabled is True
    assert decision.web_acquisition_config.production_network_enabled is False
    assert decision.web_acquisition_config.production_writes_enabled is False
    assert decision.local_fake_provider_route_allowed is True
    assert decision.live_authority_allowed is False
    assert boundary is not None
    assert boundary.fixture_only is True
    assert boundary.live_authority_allowed is False
    assert boundary.tool_host_execution_allowed is False

    direct = asyncio.run(
        boundary.execute_tool("WebSearch", {"query": "  opencode docs  "}, None)
    )
    search = asyncio.run(
        boundary.execute_tool("FixtureWebSearch", {"query": "  opencode docs  "}, None)
    )
    fetch = asyncio.run(
        boundary.execute_tool(
            "FixtureWebFetch",
            {"url": "https://docs.example.com/opencode"},
            {"turnId": "turn-router-1"},
        )
    )
    encoded = json.dumps(
        [search.model_dump(by_alias=True), fetch.model_dump(by_alias=True)],
        sort_keys=True,
    )

    assert direct.status == "blocked"
    assert direct.error_code == "opencode_fixture_tool_required"
    assert provider.calls == ["search", "fetch"]
    assert search.status == "ok"
    assert fetch.status == "ok"
    assert search.output["toolName"] == "FixtureWebSearch"
    assert fetch.output["toolName"] == "FixtureWebFetch"
    assert search.transcript_output["toolName"] == "FixtureWebSearch"
    assert fetch.transcript_output["toolName"] == "FixtureWebFetch"
    assert search.metadata["toolName"] == "FixtureWebSearch"
    assert fetch.metadata["toolName"] == "FixtureWebFetch"
    assert search.output["providerId"] == OPENCODE_WEB_FAKE_PROVIDER_ID
    assert fetch.output["providerId"] == OPENCODE_WEB_FAKE_PROVIDER_ID
    assert search.output["resultRefs"][0].startswith("source:web:")
    assert search.output["resultRefs"] != ["source:web:src_1"]
    assert fetch.output["inspectedSourceRefs"][0].startswith("source:web:")
    assert fetch.output["inspectedSourceRefs"] != ["source:web:src_1"]
    assert search.output["sources"][0]["evidenceRef"].startswith("evidence:web:")
    assert search.metadata["attachmentFlags"]["networkFetched"] is False
    assert fetch.metadata["attachmentFlags"]["liveToolDispatched"] is False
    assert "unsafe-token" not in encoded
    assert "Cookie:" not in encoded
    assert "sid=unsafe" not in encoded
    assert "providerLog" not in encoded
    assert "/Users/kevin" not in encoded
    assert '"toolName": "WebSearch"' not in encoded
    assert '"toolName": "WebFetch"' not in encoded


def test_opencode_web_router_rejects_marker_spoofed_provider_before_call() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    provider = FakeOpenCodeWebProvider()
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )

    with pytest.raises(ValueError, match="sealed OpenCode fixture provider"):
        issue_opencode_fixture_provider_handle(provider)

    assert build_opencode_web_research_tool_boundary(decision, provider_handle=provider) is None
    assert provider.calls == []


def test_opencode_web_router_rejects_url_only_search_results_as_evidence() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OpenCodeLocalFixtureWebProvider,
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    provider = OpenCodeLocalFixtureWebProvider(
        search_payload={
            "results": [
                {
                    "title": "URL only",
                    "url": "https://docs.example.com/url-only",
                }
            ]
        }
    )
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=issue_opencode_fixture_provider_handle(provider),
    )

    assert boundary is not None
    result = asyncio.run(
        boundary.execute_tool("FixtureWebSearch", {"query": "url only"}, {"turnId": "turn-1"})
    )

    assert result.status == "blocked"
    assert result.error_code == "opencode_url_only_source_evidence_blocked"
    assert result.llm_output is None
    assert result.transcript_output is None
    assert provider.calls == ["search"]


def test_opencode_web_router_rejects_url_only_fetch_results_as_evidence() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OpenCodeLocalFixtureWebProvider,
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    provider = OpenCodeLocalFixtureWebProvider(
        fetch_payload={
            "url": "https://docs.example.com/url-only",
            "title": "URL only fetch",
        }
    )
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=issue_opencode_fixture_provider_handle(provider),
    )

    assert boundary is not None
    result = asyncio.run(
        boundary.execute_tool(
            "FixtureWebFetch",
            {"url": "https://docs.example.com/url-only"},
            {"turnId": "turn-1"},
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "opencode_url_only_source_evidence_blocked"
    assert result.llm_output is None
    assert result.transcript_output is None
    assert provider.calls == ["fetch"]


def test_opencode_web_router_rejects_empty_or_redaction_only_source_content() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OpenCodeLocalFixtureWebProvider,
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    search_provider = OpenCodeLocalFixtureWebProvider(
        search_payload={
            "results": [
                {
                    "title": "Whitespace only",
                    "url": "https://docs.example.com/empty",
                    "snippet": "   ",
                }
            ]
        }
    )
    fetch_provider = OpenCodeLocalFixtureWebProvider(
        fetch_payload={
            "url": "https://docs.example.com/redacted-only",
            "title": "Redacted only",
            "content": "/Users/kevin/private/source.txt",
        }
    )
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    search_boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=issue_opencode_fixture_provider_handle(search_provider),
    )
    fetch_boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=issue_opencode_fixture_provider_handle(fetch_provider),
    )

    assert search_boundary is not None
    assert fetch_boundary is not None
    search_result = asyncio.run(
        search_boundary.execute_tool(
            "FixtureWebSearch",
            {"query": "empty"},
            {"turnId": "turn-1"},
        )
    )
    fetch_result = asyncio.run(
        fetch_boundary.execute_tool(
            "FixtureWebFetch",
            {"url": "https://docs.example.com/redacted-only"},
            {"turnId": "turn-1"},
        )
    )

    assert search_result.status == "blocked"
    assert search_result.error_code == "opencode_empty_source_evidence_blocked"
    assert search_result.llm_output is None
    assert fetch_result.status == "blocked"
    assert fetch_result.error_code == "opencode_empty_source_evidence_blocked"
    assert fetch_result.transcript_output is None


def test_opencode_web_router_rejects_mixed_supported_and_unsupported_search_results() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OpenCodeLocalFixtureWebProvider,
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    provider = OpenCodeLocalFixtureWebProvider(
        search_payload={
            "results": [
                {
                    "title": "Supported",
                    "url": "https://docs.example.com/supported",
                    "snippet": "Supported fixture-backed content.",
                },
                {
                    "title": "Whitespace only",
                    "url": "https://docs.example.com/empty",
                    "snippet": "   ",
                },
                {
                    "title": "Private path only",
                    "url": "https://docs.example.com/redacted",
                    "snippet": "/Users/kevin/private/source.txt",
                },
            ]
        }
    )
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=issue_opencode_fixture_provider_handle(provider),
    )

    assert boundary is not None
    result = asyncio.run(
        boundary.execute_tool(
            "FixtureWebSearch",
            {"query": "mixed"},
            {"turnId": "turn-1"},
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code == "opencode_empty_source_evidence_blocked"
    assert result.llm_output is None
    assert "source:web:" not in encoded
    assert "evidence:web:" not in encoded


def test_opencode_web_router_strips_nested_direct_tool_claim_metadata() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OpenCodeLocalFixtureWebProvider,
        build_opencode_web_research_tool_boundary,
        issue_opencode_fixture_provider_handle,
        materialize_opencode_web_provider_router,
    )

    provider = OpenCodeLocalFixtureWebProvider(
        search_payload={
            "results": [
                {
                    "title": "Nested claim",
                    "url": "https://docs.example.com/nested",
                    "snippet": "Fixture-backed content.",
                    "metadata": {
                        "toolName": "WebSearch",
                        "operation": "WebFetch",
                        "method": "WebSearch",
                        "action": "searched",
                        "note": "direct WebSearch executed successfully",
                        "summary": "WebFetch opened this source",
                        "quality": 0.93,
                    },
                }
            ]
        }
    )
    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )
    boundary = build_opencode_web_research_tool_boundary(
        decision,
        provider_handle=issue_opencode_fixture_provider_handle(provider),
    )

    assert boundary is not None
    result = asyncio.run(
        boundary.execute_tool(
            "FixtureWebSearch",
            {"query": "nested claim"},
            {"turnId": "turn-1"},
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)

    assert result.status == "ok"
    assert result.output["sources"][0]["metadata"] == {"quality": 0.93}
    assert '"toolName": "WebSearch"' not in encoded
    assert '"operation": "WebFetch"' not in encoded
    assert '"method": "WebSearch"' not in encoded
    assert '"action": "searched"' not in encoded
    assert "direct WebSearch executed successfully" not in encoded
    assert "WebFetch opened this source" not in encoded


def test_opencode_web_router_decision_rejects_forged_live_authority_and_refs() -> None:
    from openmagi_core_agent.web_acquisition.opencode_provider_router import (
        OPENCODE_WEB_FAKE_PROVIDER_ID,
        OpenCodeWebProviderRouterDecision,
        materialize_opencode_web_provider_router,
    )
    from openmagi_core_agent.web_acquisition.provider_boundary import (
        WebAcquisitionConfig,
    )

    decision = materialize_opencode_web_provider_router(
        profile_key="scout_web_docs",
        rollout_enabled=True,
        fake_provider_boundary_enabled=True,
        local_fake_provider_available=True,
    )

    with pytest.raises(ValidationError, match="literal"):
        OpenCodeWebProviderRouterDecision.model_construct(
            status="ready",
            profileKey="scout_web_docs",
            reasonCodes=("fake_provider_route_ready",),
            toolNames=("FixtureWebSearch", "FixtureWebFetch"),
            providerId=OPENCODE_WEB_FAKE_PROVIDER_ID,
            webAcquisitionConfig=decision.web_acquisition_config,
            liveAuthorityAllowed=True,
            modelCallsAllowed=True,
            workspaceMutationAllowed=True,
            attachmentFlags={name: False for name in decision.attachment_flags},
        )

    with pytest.raises(ValidationError, match="toolNames"):
        decision.model_copy(update={"toolNames": ("WebSearch", "FixtureWebFetch")})

    with pytest.raises(ValidationError, match="providerId"):
        decision.model_copy(update={"providerId": "live.search.provider"})

    with pytest.raises(ValidationError, match="reasonCodes"):
        decision.model_copy(update={"reasonCodes": ("live_network_allowed",)})

    forged_config = WebAcquisitionConfig.model_construct(
        enabled=True,
        localFakeProviderEnabled=True,
        providerId=OPENCODE_WEB_FAKE_PROVIDER_ID,
        productionNetworkEnabled=True,
    )
    with pytest.raises(ValidationError, match="webAcquisitionConfig"):
        decision.model_copy(update={"webAcquisitionConfig": forged_config})


def test_opencode_web_router_import_boundary_has_no_live_provider_or_network_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "web_acquisition"
        / "opencode_provider_router.py"
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
        "google.adk.runners",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.runtime.provider_execution",
        "openmagi_core_agent.web_acquisition.live_provider_pack",
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
