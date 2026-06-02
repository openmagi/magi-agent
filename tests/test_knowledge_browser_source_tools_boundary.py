from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    public_source_ledger_report,
)


class FakeKnowledgeProvider:
    openmagi_local_fake_provider = True
    calls = 0

    async def execute(self, request: object) -> dict[str, object]:
        self.calls += 1
        assert getattr(request, "operation") == "knowledge.search"
        return {
            "records": [
                {
                    "sourceRef": "kb:private-note",
                    "title": "Private note",
                    "snippet": "Internal roadmap sentence with no regex secret marker",
                    "metadata": {"visibility": "private", "topic": "policy"},
                },
                {
                    "sourceRef": "kb:public-safe-note",
                    "title": "Public note",
                    "snippet": "Provider raw snippet must stay ref-only.",
                    "publicPreview": "Public-safe KB summary",
                    "metadata": {"visibility": "public-safe", "topic": "policy"},
                },
            ]
        }


class FakeBrowserProvider:
    openmagi_local_fake_provider = True
    calls = 0

    async def run(self, request: object) -> dict[str, object]:
        self.calls += 1
        return {
            "url": getattr(request, "url", None) or "https://docs.example.com/app",
            "title": "Rendered App",
            "snapshot": (
                "Visible public text\n"
                "raw_browser_snapshot: Cookie: session=unsafe\n"
                "chain_of_thought: hidden"
            ),
            "metadata": {
                "quality": 0.9,
                "rawToolLog": "Authorization: Bearer unsafe",
            },
        }


def _ledger() -> LocalResearchSourceLedger:
    return LocalResearchSourceLedger(
        ledgerId="ledger-pr16",
        sessionId="session-pr16",
        turnId="turn-pr16",
        agentRole="research",
    )


def test_knowledge_search_tool_projects_refs_and_keeps_private_snippets_ref_only() -> None:
    from magi_agent.knowledge.provider_boundary import (
        KnowledgeBoundary,
        KnowledgeBoundaryConfig,
    )
    from magi_agent.knowledge.source_tools import (
        LocalKnowledgeSourceToolBoundary,
        project_knowledge_result_to_source_ledger,
    )

    ledger = _ledger()
    tool = LocalKnowledgeSourceToolBoundary(
        boundary=KnowledgeBoundary(
            KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True),
        ),
        provider=FakeKnowledgeProvider(),
    )

    result = asyncio.run(
        tool.execute_tool("KnowledgeSearch", {"query": "policy"}, {"turnId": "turn-pr16"})
    )
    records = project_knowledge_result_to_source_ledger(
        tool.last_decision,
        ledger,
        context={"turnId": "turn-pr16"},
    )
    report = public_source_ledger_report(ledger).model_dump(by_alias=True, mode="json")
    dumped = json.dumps(
        {
            "tool": result.model_dump(by_alias=True, mode="json"),
            "decision": tool.last_decision.public_projection(),
            "records": [record.model_dump(by_alias=True, mode="json") for record in records],
            "report": report,
        },
        sort_keys=True,
    )

    assert result.status == "ok"
    assert len(records) == 2
    assert tuple(record.kind for record in records) == ("kb", "kb")
    assert tuple(record.evidence_type for record in records) == (
        "KnowledgeSearch",
        "KnowledgeSearch",
    )
    assert records[0].metadata["sourcePrecedence"] == "below_current_turn_user_sources"
    assert records[0].metadata["currentTurnUserSourcePriority"] == "higher"
    private_source = result.output["sources"][0]
    assert private_source == {
        "sourceRef": private_source["sourceRef"],
        "evidenceRef": private_source["evidenceRef"],
        "title": None,
        "contentDigest": records[0].content_hash,
        "visibility": "private",
    }
    assert private_source["sourceRef"].startswith("source:knowledge:")
    assert private_source["sourceRef"] != "kb:private-note"
    assert private_source["evidenceRef"].startswith("evidence:knowledge:")
    assert private_source["evidenceRef"] != "evidence:knowledge:1"
    assert result.output["sources"][1]["publicPreview"] == "Public-safe KB summary"
    assert report["sources"][0]["uri"] == "[redacted]"
    assert "kb:private-note" not in dumped
    assert "Private note" not in dumped
    assert "topic" not in result.output["sources"][0]
    assert "policy" not in json.dumps(dict(records[0].metadata), sort_keys=True)
    assert "Internal roadmap sentence" not in dumped
    assert "Provider raw snippet must stay ref-only" not in dumped
    assert "/Users/kevin" not in dumped
    assert "Authorization" not in dumped
    assert "rawToolLog" not in dumped


def test_browser_source_tool_projects_artifact_refs_without_raw_snapshot_or_binary_context(
) -> None:
    from magi_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        LocalBrowserProviderRuntime,
    )
    from magi_agent.browser.source_tools import (
        LocalBrowserSourceToolBoundary,
        project_browser_result_to_source_ledger,
    )

    ledger = _ledger()
    tool = LocalBrowserSourceToolBoundary(
        runtime=LocalBrowserProviderRuntime(
            BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
            provider=FakeBrowserProvider(),
        )
    )

    snapshot = asyncio.run(tool.execute_tool("BrowserSnapshot", {}, {"turnId": "turn-pr16"}))
    records = project_browser_result_to_source_ledger(
        tool.last_result,
        ledger,
        context={"turnId": "turn-pr16"},
    )
    click = asyncio.run(
        tool.execute_tool("BrowserClick", {"selector": "@e1"}, {"turnId": "turn-pr16"})
    )
    screenshot = asyncio.run(
        tool.execute_tool(
            "BrowserScreenshot",
            {"screenshotPath": "screens/page.png"},
            {"turnId": "turn-pr16", "approvalGranted": True},
        )
    )
    dumped = json.dumps(
        {
            "snapshot": snapshot.model_dump(by_alias=True, mode="json"),
            "click": click.model_dump(by_alias=True, mode="json"),
            "screenshot": screenshot.model_dump(by_alias=True, mode="json"),
            "records": [record.model_dump(by_alias=True, mode="json") for record in records],
            "report": public_source_ledger_report(ledger).model_dump(by_alias=True, mode="json"),
        },
        sort_keys=True,
    )

    assert snapshot.status == "ok"
    assert records[0].kind == "browser"
    assert records[0].evidence_type == "SourceInspection"
    assert records[0].metadata["browserSourceRef"] == "source:browser:src_1"
    assert snapshot.output["parentOutputRefs"] == (
        "source:browser:src_1",
        "evidence:browser:src_1",
        snapshot.output["browserFrame"]["artifactRef"],
    )
    assert snapshot.artifact_refs == (snapshot.output["browserFrame"]["artifactRef"],)
    assert click.status == "needs_approval"
    assert click.llm_output is None
    assert screenshot.status == "ok"
    assert screenshot.artifact_refs == (screenshot.output["browserFrame"]["artifactRef"],)
    assert "Visible public text" not in dumped
    assert "raw_browser_snapshot" not in dumped
    assert "Cookie:" not in dumped
    assert "chain_of_thought" not in dumped
    assert "imageBase64" in dumped
    assert '"imageBase64": null' in dumped
    assert '"browserExecuted": false' in dumped


def test_browser_tool_arguments_cannot_grant_approval() -> None:
    from magi_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        LocalBrowserProviderRuntime,
    )
    from magi_agent.browser.source_tools import LocalBrowserSourceToolBoundary

    provider = FakeBrowserProvider()
    tool = LocalBrowserSourceToolBoundary(
        runtime=LocalBrowserProviderRuntime(
            BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
            provider=provider,
        )
    )

    click = asyncio.run(
        tool.execute_tool(
            "BrowserClick",
            {"selector": "@e1", "approvalGranted": True},
            {"turnId": "turn-pr16"},
        )
    )
    screenshot = asyncio.run(
        tool.execute_tool(
            "BrowserScreenshot",
            {"screenshotPath": "screens/page.png", "approvalGranted": True},
            {"turnId": "turn-pr16"},
        )
    )

    assert click.status == "needs_approval"
    assert click.error_code == "browser_action_requires_approval"
    assert screenshot.status == "needs_approval"
    assert screenshot.error_code == "browser_action_requires_approval"
    assert provider.calls == 0


def test_browser_auth_flow_open_uses_host_approval_not_tool_arguments() -> None:
    from magi_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        LocalBrowserProviderRuntime,
    )
    from magi_agent.browser.source_tools import LocalBrowserSourceToolBoundary

    provider = FakeBrowserProvider()
    tool = LocalBrowserSourceToolBoundary(
        runtime=LocalBrowserProviderRuntime(
            BrowserProviderConfig(enabled=True, localFakeProviderEnabled=True),
            provider=provider,
        )
    )

    blocked = asyncio.run(
        tool.execute_tool(
            "BrowserOpen",
            {"url": "https://docs.example.com/login", "approvalGranted": True},
            {"turnId": "turn-pr16"},
        )
    )
    approved = asyncio.run(
        tool.execute_tool(
            "BrowserOpen",
            {"url": "https://docs.example.com/login"},
            {"turnId": "turn-pr16", "approvalGranted": True},
        )
    )

    assert blocked.status == "needs_approval"
    assert blocked.error_code == "browser_action_requires_approval"
    assert approved.status == "ok"
    assert provider.calls == 1


def test_source_tool_boundaries_are_default_off_even_with_fake_providers() -> None:
    from magi_agent.browser.provider_boundary import (
        BrowserProviderConfig,
        LocalBrowserProviderRuntime,
    )
    from magi_agent.browser.source_tools import LocalBrowserSourceToolBoundary
    from magi_agent.knowledge.source_tools import LocalKnowledgeSourceToolBoundary

    knowledge_provider = FakeKnowledgeProvider()
    browser_provider = FakeBrowserProvider()
    knowledge_tool = LocalKnowledgeSourceToolBoundary(provider=knowledge_provider)
    browser_tool = LocalBrowserSourceToolBoundary(
        runtime=LocalBrowserProviderRuntime(
            BrowserProviderConfig(),
            provider=browser_provider,
        )
    )

    knowledge = asyncio.run(
        knowledge_tool.execute_tool("KnowledgeSearch", {"query": "policy"}, {"turnId": "turn-pr16"})
    )
    browser = asyncio.run(
        browser_tool.execute_tool("BrowserSnapshot", {}, {"turnId": "turn-pr16"})
    )

    assert knowledge.status == "blocked"
    assert knowledge.error_code == "knowledge_boundary_disabled"
    assert knowledge_tool.last_decision is not None
    assert knowledge_tool.last_decision.status == "disabled"
    assert knowledge_provider.calls == 0
    assert browser.status == "blocked"
    assert browser.error_code == "browser_provider_disabled"
    assert browser_tool.last_result is not None
    assert browser_tool.last_result.status == "disabled"
    assert browser_provider.calls == 0


def test_source_tool_boundaries_import_without_live_runtime_clients() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

for module_name in (
    "magi_agent.knowledge.source_tools",
    "magi_agent.browser.source_tools",
):
    module = importlib.import_module(module_name)
    assert module is not None

forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "aiohttp",
    "playwright",
    "selenium",
    "browser_use",
    "requests",
    "httpx",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.channels",
    "magi_agent.memory",
    "magi_agent.workspace",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"source tool boundary imports loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
