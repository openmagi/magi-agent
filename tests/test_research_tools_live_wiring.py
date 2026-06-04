from __future__ import annotations

import asyncio
import json

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    public_source_ledger_report,
)
from magi_agent.tools.context import ToolContext
from magi_agent.web_acquisition.live_provider_pack import (
    LiveWebAcquisitionPackConfig,
    LiveWebAcquisitionProviderPack,
    StubLiveProvider,
    WebAcquisitionProviderRequest,
    WebAcquisitionProviderResult,
)
from magi_agent.web_acquisition.research_tools import (
    LIVE_WEB_ACQUISITION_ENABLED_ENV,
    LIVE_WEB_ACQUISITION_KILL_SWITCH_ENV,
    LocalWebResearchToolBoundary,
    _live_request_from_tool,
    live_web_acquisition_active,
    project_live_web_acquisition_result_to_source_ledger,
)


class LegacyRuntimeSpy:
    """Records whether the legacy fixture runtime path was taken."""

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[object] = []

    async def run(self, request: object) -> object:
        self.calls.append(request)
        return self._result


class LiveProviderSpy(StubLiveProvider):
    """StubLiveProvider that records which live operations were dispatched."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return dict(super().search(request))  # type: ignore[arg-type]

    def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return dict(super().fetch(request))  # type: ignore[arg-type]


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-1",
        sessionId="session-1",
        sessionKey="session-key-1",
        userId="owner-1",
        turnId="turn-1",
        toolUseId="toolu-web-1",
    )


def _live_pack() -> LiveWebAcquisitionProviderPack:
    return LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(
            enabled=True,
            liveNetworkEnabled=True,
            providerAllowlist=("web.search", "web.fetch"),
        )
    )


def _on_env() -> dict[str, str]:
    return {LIVE_WEB_ACQUISITION_ENABLED_ENV: "1"}


def _legacy_ok_result() -> object:
    # Minimal stand-in is unnecessary; tests for the legacy path only assert the
    # spy was invoked, so the runtime result is never projected here.
    return object()


def test_live_gate_helper_requires_enabled_and_not_killed() -> None:
    assert live_web_acquisition_active(env={}) is False
    assert live_web_acquisition_active(env={LIVE_WEB_ACQUISITION_ENABLED_ENV: "1"}) is True
    assert (
        live_web_acquisition_active(
            env={
                LIVE_WEB_ACQUISITION_ENABLED_ENV: "1",
                LIVE_WEB_ACQUISITION_KILL_SWITCH_ENV: "1",
            }
        )
        is False
    )


def test_default_boundary_uses_legacy_runtime_and_never_takes_live_path() -> None:
    runtime = LegacyRuntimeSpy(_legacy_ok_result())
    boundary = LocalWebResearchToolBoundary(runtime=runtime)

    # Default construction: no live pack/provider, no env -> legacy runtime.
    # The legacy result is not a WebAcquisitionResult, so anything beyond the
    # runtime.run call would raise an AttributeError when the code tries to
    # access .status on the plain object(); we only assert the runtime was hit.
    try:
        asyncio.run(boundary.execute_tool("WebSearch", {"query": "x"}, _context()))
    except AttributeError:
        pass

    assert len(runtime.calls) == 1
    assert boundary.last_live_result is None


def test_gate_off_with_live_pack_injected_still_uses_legacy_runtime() -> None:
    runtime = LegacyRuntimeSpy(_legacy_ok_result())
    provider = LiveProviderSpy()
    boundary = LocalWebResearchToolBoundary(
        runtime=runtime,
        live_pack=_live_pack(),
        live_provider=provider,
        env={},  # gate OFF
    )

    # Gate is off, so falls through to legacy runtime whose result is a plain
    # object() that lacks .status — expect AttributeError when code checks it.
    try:
        asyncio.run(boundary.execute_tool("WebSearch", {"query": "x"}, _context()))
    except AttributeError:
        pass

    assert len(runtime.calls) == 1
    assert provider.calls == []  # live_pack.run never reached
    assert boundary.last_live_result is None


def test_gate_on_websearch_drives_live_pack_with_stub_records() -> None:
    runtime = LegacyRuntimeSpy(_legacy_ok_result())
    provider = LiveProviderSpy()
    boundary = LocalWebResearchToolBoundary(
        runtime=runtime,
        live_pack=_live_pack(),
        live_provider=provider,
        env=_on_env(),
    )

    result = asyncio.run(
        boundary.execute_tool("WebSearch", {"query": "stub live"}, _context())
    )

    assert runtime.calls == []  # legacy path NOT taken
    assert provider.calls == ["search"]
    assert result.status == "ok"
    assert isinstance(boundary.last_live_result, WebAcquisitionProviderResult)
    assert boundary.last_live_result.status == "ok"

    output = result.output
    assert isinstance(output, dict)
    assert output["toolName"] == "WebSearch"
    assert output["sources"], "expected at least one live source record"
    assert result.llm_output == output
    assert isinstance(result.transcript_output, dict)
    assert result.transcript_output["toolName"] == "WebSearch"
    assert result.metadata["boundaryStatus"] == "ok"


def test_gate_on_webfetch_blocked_ssrf_url_returns_non_ok_and_pack_enforced() -> None:
    runtime = LegacyRuntimeSpy(_legacy_ok_result())
    provider = LiveProviderSpy()
    boundary = LocalWebResearchToolBoundary(
        runtime=runtime,
        live_pack=_live_pack(),
        live_provider=provider,
        env=_on_env(),
    )

    result = asyncio.run(
        boundary.execute_tool(
            "WebFetch",
            {"url": "http://169.254.169.254/latest/meta-data/"},
            _context(),
        )
    )

    assert runtime.calls == []
    assert provider.calls == []  # SSRF firewall stops before provider call
    assert result.status in {"blocked", "error", "needs_approval"}
    assert result.status != "ok"
    assert result.output is None
    assert boundary.last_live_result is not None
    assert boundary.last_live_result.status != "ok"


def test_websearch_and_webfetch_in_same_turn_produce_distinct_request_ids() -> None:
    """WebSearch and WebFetch with the same turn_id must build distinct requestIds.

    A turn that issues both a search and a fetch would formerly collide because
    requestId was derived from turn_id alone. The fix includes tool_name (and
    tool_use_id when present) as discriminators.
    """
    context = _context()  # turn_id="turn-1", toolUseId="toolu-web-1"

    search_req = _live_request_from_tool("WebSearch", {"query": "collision test"}, context)
    fetch_req = _live_request_from_tool("WebFetch", {"url": "https://docs.example.com/stub-fetch"}, context)

    assert isinstance(search_req, WebAcquisitionProviderRequest)
    assert isinstance(fetch_req, WebAcquisitionProviderRequest)
    assert search_req.request_id != fetch_req.request_id, (
        f"requestId collision: both WebSearch and WebFetch produced '{search_req.request_id}'"
    )


def test_projected_live_tool_result_leaks_no_raw_url_or_secret() -> None:
    provider = LiveProviderSpy()
    boundary = LocalWebResearchToolBoundary(
        runtime=LegacyRuntimeSpy(_legacy_ok_result()),
        live_pack=_live_pack(),
        live_provider=provider,
        env=_on_env(),
    )

    result = asyncio.run(
        boundary.execute_tool(
            "WebFetch",
            {"url": "https://docs.example.com/stub-fetch"},
            _context(),
        )
    )
    encoded = json.dumps(result.model_dump(by_alias=True, mode="python"), sort_keys=True)

    assert result.status == "ok"
    # The live record carries a redacted urlRef (url:<digest>), never the raw URL.
    assert "docs.example.com/stub-fetch" not in encoded
    assert "https://docs.example.com" not in encoded


def test_project_live_result_to_source_ledger_parity() -> None:
    provider = LiveProviderSpy()
    boundary = LocalWebResearchToolBoundary(
        runtime=LegacyRuntimeSpy(_legacy_ok_result()),
        live_pack=_live_pack(),
        live_provider=provider,
        env=_on_env(),
    )
    context = _context()
    asyncio.run(boundary.execute_tool("WebSearch", {"query": "stub live"}, context))
    live_result = boundary.last_live_result

    ledger = LocalResearchSourceLedger(
        ledgerId="ledger-1",
        sessionId="session-1",
        turnId="turn-1",
        agentRole="research",
    )
    records = project_live_web_acquisition_result_to_source_ledger(
        live_result,
        ledger,
        context=context,
        tool_name="WebSearch",
    )
    report = public_source_ledger_report(ledger)
    dumped_report = json.dumps(report.model_dump(by_alias=True), sort_keys=True)

    assert len(records) == 1
    assert records[0].turn_id == "turn-1"
    assert records[0].tool_name == "WebSearch"
    assert records[0].tool_use_id == "toolu-web-1"
    assert records[0].evidence_type == "SourceInspection"
    assert records[0].kind == "web_search"
    assert records[0].content_hash is not None
    assert "docs.example.com" not in dumped_report
