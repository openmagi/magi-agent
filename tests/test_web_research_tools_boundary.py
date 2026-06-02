from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    public_source_ledger_report,
)
from magi_agent.tools.context import ToolContext
from magi_agent.web_acquisition.provider_boundary import (
    LocalWebAcquisitionRuntime,
    WebAcquisitionConfig,
)
from magi_agent.web_acquisition.research_tools import (
    LocalWebResearchToolBoundary,
    project_web_acquisition_result_to_source_ledger,
)


class FakeResearchProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return {
            "results": [
                {
                    "title": " Current Docs ",
                    "url": "https://docs.example.com/current?utm_source=ad",
                    "snippet": "Public source summary with sk-unsafe-secret.",
                    "metadata": {
                        "quality": 0.91,
                        "providerLog": "Authorization: Bearer unsafe-token",
                        "rawUrl": "https://signed.example.com/object?token=unsafe",
                    },
                }
            ],
            "preview": "Visible preview with Cookie: session=unsafe",
            "rawPayload": {"private": "/Users/kevin/private/provider.json"},
        }

    async def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return {
            "url": "https://docs.example.com/current",
            "title": "Docs",
            "content": (
                "Rendered public content.\n"
                "raw_tool_log: Cookie: session=unsafe\n"
                "/Users/kevin/private/source.txt"
            ),
            "metadata": {
                "status": 200,
                "contentType": "text/html",
                "cookie": "session=unsafe",
                "providerLog": "raw provider payload",
            },
        }


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionId="session-1",
        turnId="turn-1",
        toolUseId="toolu-web-1",
    )


def test_web_research_tools_default_off_returns_blocked_without_provider_call() -> None:
    provider = FakeResearchProvider()
    boundary = LocalWebResearchToolBoundary(
        runtime=LocalWebAcquisitionRuntime(WebAcquisitionConfig(), provider=provider)
    )

    result = asyncio.run(
        boundary.execute_tool(
            "WebSearch",
            {"query": " latest model "},
            _context(),
        )
    )

    assert result.status == "blocked"
    assert result.error_code == "web_acquisition_disabled"
    assert result.output is None
    assert result.llm_output is None
    assert result.transcript_output is None
    assert provider.calls == []
    assert result.metadata["boundaryStatus"] == "disabled"
    assert result.metadata["attachmentFlags"]["networkFetched"] is False
    assert result.metadata["attachmentFlags"]["liveToolDispatched"] is False


def test_web_research_tool_boundary_declares_fixture_only_not_toolhost_execution() -> None:
    boundary = LocalWebResearchToolBoundary()

    assert boundary.fixture_only is True
    assert boundary.tool_host_execution_allowed is False
    assert boundary.live_authority_allowed is False


def test_web_research_tools_block_private_urls_before_provider_call_and_redact_raw_url() -> None:
    provider = FakeResearchProvider()
    boundary = LocalWebResearchToolBoundary(
        runtime=LocalWebAcquisitionRuntime(
            WebAcquisitionConfig(enabled=True, localFakeProviderEnabled=True),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "WebFetch",
            {"url": "https://docs.example.com/current?token=unsafe"},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "blocked"
    assert result.error_code == "credential_url_blocked"
    assert provider.calls == []
    assert "token=unsafe" not in encoded
    assert "https://docs.example.com/current" not in encoded
    assert result.metadata["attachmentFlags"]["networkFetched"] is False


def test_web_search_fake_provider_returns_sanitized_deterministic_tool_result() -> None:
    provider = FakeResearchProvider()
    boundary = LocalWebResearchToolBoundary(
        runtime=LocalWebAcquisitionRuntime(
            WebAcquisitionConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.search",
            ),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "WebSearch",
            {"query": "  current   source  "},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)
    output = result.output

    assert provider.calls == ["search"]
    assert result.status == "ok"
    assert isinstance(output, dict)
    assert output["toolName"] == "WebSearch"
    assert output["query"] == "current source"
    assert output["providerId"] == "fake.search"
    assert output["resultRefs"] == ["source:web:src_1"]
    assert output["sources"] == [
        {
            "sourceRef": "source:web:src_1",
            "evidenceRef": "evidence:web:src_1",
            "title": "Current Docs",
            "urlRef": "https://docs.example.com/current?utm_source=ad",
            "contentDigest": output["sources"][0]["contentDigest"],
            "proofType": "observed",
            "metadata": {"quality": 0.91},
        }
    ]
    assert output["sources"][0]["contentDigest"].startswith("sha256:")
    assert result.llm_output == output
    assert result.transcript_output == {
        "toolName": "WebSearch",
        "resultRefs": ["source:web:src_1"],
    }
    assert "rawPayload" not in encoded
    assert "providerLog" not in encoded
    assert "Authorization" not in encoded
    assert "unsafe-token" not in encoded
    assert "sk-unsafe-secret" not in encoded
    assert "/Users/kevin" not in encoded
    assert "session=unsafe" not in encoded
    assert result.metadata["attachmentFlags"]["networkFetched"] is False


def test_web_fetch_fake_provider_returns_sanitized_preview_status_and_source_refs() -> None:
    provider = FakeResearchProvider()
    boundary = LocalWebResearchToolBoundary(
        runtime=LocalWebAcquisitionRuntime(
            WebAcquisitionConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.fetch",
            ),
            provider=provider,
        )
    )

    result = asyncio.run(
        boundary.execute_tool(
            "WebFetch",
            {"url": "https://docs.example.com/current"},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)
    output = result.output

    assert provider.calls == ["fetch"]
    assert result.status == "ok"
    assert isinstance(output, dict)
    assert output["toolName"] == "WebFetch"
    assert output["url"] == "https://docs.example.com/current"
    assert output["providerId"] == "fake.fetch"
    assert output["status"] == 200
    assert output["statusClass"] == "2xx"
    assert output["contentType"] == "text/html"
    assert output["inspectedSourceRefs"] == ["source:web:src_1"]
    assert output["publicPreview"] == "Rendered public content.\n[redacted-path]"
    assert output["sources"][0]["proofType"] == "opened"
    assert output["sources"][0]["urlRef"] == "https://docs.example.com/current"
    assert output["sources"][0]["contentDigest"].startswith("sha256:")
    assert "raw_tool_log" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded
    assert "providerLog" not in encoded
    assert "/Users/kevin" not in encoded


def test_project_web_acquisition_result_records_current_turn_source_ledger_metadata() -> None:
    provider = FakeResearchProvider()
    boundary = LocalWebResearchToolBoundary(
        runtime=LocalWebAcquisitionRuntime(
            WebAcquisitionConfig(
                enabled=True,
                localFakeProviderEnabled=True,
                providerId="fake.search",
            ),
            provider=provider,
        )
    )
    context = _context()
    tool_result = asyncio.run(
        boundary.execute_tool("WebSearch", {"query": "current source"}, context)
    )
    web_result = boundary.last_result
    ledger = LocalResearchSourceLedger(
        ledgerId="ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )

    records = project_web_acquisition_result_to_source_ledger(
        web_result,
        ledger,
        context=context,
        tool_name="WebSearch",
    )
    report = public_source_ledger_report(ledger)
    dumped_report = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert tool_result.status == "ok"
    assert len(records) == 1
    assert records[0].source_id == "src_1"
    assert records[0].turn_id == "turn-1"
    assert records[0].tool_name == "WebSearch"
    assert records[0].tool_use_id == "toolu-web-1"
    assert records[0].evidence_type == "SourceInspection"
    assert records[0].kind == "web_search"
    assert records[0].content_hash is not None
    assert records[0].metadata["providerId"] == "fake.search"
    assert records[0].metadata["webAcquisitionSourceRef"] == "source:web:src_1"
    assert records[0].metadata["evidenceId"] == "evidence:web:src_1"
    assert records[0].attachment_flags.live_tool_dispatched is False
    assert records[0].attachment_flags.source_fetched is False
    assert report.attachment_flags.live_tool_dispatched is False
    assert "Authorization" not in dumped_report
    assert "unsafe-token" not in dumped_report
    assert "providerLog" not in dumped_report
    assert "rawUrl" not in dumped_report
    assert "token=unsafe" not in dumped_report


def test_web_research_tools_import_boundary_has_no_live_or_network_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "web_acquisition"
        / "research_tools.py"
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
        "magi_agent.adk_bridge",
        "magi_agent.browser",
        "magi_agent.transport",
        "magi_agent.web_acquisition.live_provider_pack",
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

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.web_acquisition.research_tools")
assert hasattr(module, "LocalWebResearchToolBoundary")

forbidden_loaded = (
    "magi_agent.adk_bridge.local_toolhost",
    "magi_agent.web_acquisition.live_provider_pack",
)
loaded = [name for name in forbidden_loaded if name in sys.modules]
if loaded:
    raise AssertionError(f"research_tools import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
