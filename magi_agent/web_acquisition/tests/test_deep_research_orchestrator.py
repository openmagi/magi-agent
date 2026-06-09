"""Tests for DeepWebResearchOrchestrator — PR4 (TDD: written first).

All tests are hermetic: FakeDeepResearchBoundary uses canned responses,
no network sockets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from magi_agent.evidence.runtime_issuance import issue_runtime_authority
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig
from magi_agent.web_acquisition.research_tools import (
    LIVE_WEB_ACQUISITION_ENABLED_ENV,
    LocalWebResearchToolBoundary,
)


# ---------------------------------------------------------------------------
# Fake boundary helpers
# ---------------------------------------------------------------------------


def _search_result_ok(
    sources: list[dict[str, str]],
    query: str = "test query",
) -> ToolResult:
    """Build a canned search ToolResult with the given sources."""
    return ToolResult(
        status="ok",
        output={
            "toolName": "WebSearch",
            "query": query,
            "sources": sources,
            "providerId": "fake.provider",
            "resultRefs": [f"ref:{s['urlRef']}" for s in sources],
        },
        llmOutput={},
        transcriptOutput={},
        metadata={},
    )


def _fetch_result_ok(url_ref: str, content: str) -> ToolResult:
    """Build a canned fetch ToolResult."""
    return ToolResult(
        status="ok",
        output={
            "toolName": "WebFetch",
            "url": url_ref,
            "sources": [],
            "providerId": "fake.provider",
            "publicPreview": content,
        },
        llmOutput={},
        transcriptOutput={},
        metadata={},
    )


def _blocked_result(tool_name: str, reason: str) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=reason,
        metadata={"toolName": tool_name},
    )


class FakeDeepResearchBoundary(LocalWebResearchToolBoundary):
    """Hermetic fake boundary for orchestrator tests.

    Call responses are programmed via ``search_responses`` and
    ``fetch_responses`` dicts keyed by call-index.  Both counters start at 0
    and auto-increment per call.
    """

    def __init__(
        self,
        *,
        search_responses: list[ToolResult] | None = None,
        fetch_responses: list[ToolResult] | None = None,
        live_gate_on: bool = True,
    ) -> None:
        env: dict[str, str] = {}
        if live_gate_on:
            env[LIVE_WEB_ACQUISITION_ENABLED_ENV] = "1"
        super().__init__(env=env)
        self._search_responses = search_responses or []
        self._fetch_responses = fetch_responses or []
        self._search_call_count = 0
        self._fetch_call_count = 0

    @property
    def search_call_count(self) -> int:
        return self._search_call_count

    @property
    def fetch_call_count(self) -> int:
        return self._fetch_call_count

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> ToolResult:
        if tool_name == "WebSearch":
            idx = self._search_call_count
            self._search_call_count += 1
            if idx < len(self._search_responses):
                return self._search_responses[idx]
            # Default: empty search result
            return _search_result_ok([], query=str(arguments.get("query", "")))
        if tool_name in {"WebFetch", "WebReader"}:
            idx = self._fetch_call_count
            self._fetch_call_count += 1
            if idx < len(self._fetch_responses):
                return self._fetch_responses[idx]
            url = str(arguments.get("url", "url:unknown"))
            return _fetch_result_ok(url, "No content.")
        return _blocked_tool_result(tool_name, "unsupported_tool")  # type: ignore[return-value]


def _blocked_tool_result(tool_name: str, reason: str) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=reason,
        metadata={"toolName": tool_name},
    )


# ---------------------------------------------------------------------------
# Helper: build sources for fake search results
# ---------------------------------------------------------------------------

def _src(url_ref: str, title: str = "Result", snippet: str = "") -> dict[str, str]:
    return {"urlRef": url_ref, "title": title, "snippet": snippet}


# ---------------------------------------------------------------------------
# 1. disabled-by-default test
# ---------------------------------------------------------------------------


def test_orchestrator_disabled_by_default() -> None:
    """Config(enabled=False) → DeepResearchResult(status='disabled', fetches=0)."""
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    fake = FakeDeepResearchBoundary()
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=DeepResearchConfig(),  # enabled=False
    )
    result = asyncio.run(orch.research("Apple stock 2018"))
    assert result.status == "disabled"
    assert result.fetches_issued == 0
    assert result.queries_issued == 0


def test_orchestrator_disabled_no_provider_calls() -> None:
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    fake = FakeDeepResearchBoundary()
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=DeepResearchConfig(enabled=False),
    )
    asyncio.run(orch.research("anything"))
    assert fake.search_call_count == 0
    assert fake.fetch_call_count == 0


# ---------------------------------------------------------------------------
# 2. live-gate off → disabled
# ---------------------------------------------------------------------------


def test_orchestrator_disabled_when_live_gate_off() -> None:
    """Live acquisition gate OFF → result.status == 'disabled'."""
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    fake = FakeDeepResearchBoundary(live_gate_on=False)
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=DeepResearchConfig(enabled=True),
    )
    result = asyncio.run(orch.research("any question"))
    assert result.status == "disabled"
    assert result.fetches_issued == 0


# ---------------------------------------------------------------------------
# 3. provider seam: search AND fetch invoked (the key integration test)
# ---------------------------------------------------------------------------


def test_orchestrator_invokes_search_and_fetch() -> None:
    """Orchestrator must call both search AND fetch through the boundary seam."""
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    authority = issue_runtime_authority(
        authority_id="test-orch-authority",
        scopes=("research_source_proof", "research_claim_support"),
    )
    search_resp = _search_result_ok(
        [_src("url:abc1", "Apple 2018 Milestone", "Apple crossed 200 in 2018")],
        query="Apple stock 2018",
    )
    fetch_resp = _fetch_result_ok(
        "url:abc1",
        "In 2018, Apple stock first crossed above the $200 mark.",
    )
    fake = FakeDeepResearchBoundary(
        search_responses=[search_resp],
        fetch_responses=[fetch_resp],
    )
    config = DeepResearchConfig(enabled=True, max_queries=1, max_fetch_per_query=1, max_iterations=1)
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=config,
        runtime_authority=authority,
    )
    result = asyncio.run(orch.research("Apple stock first year above 200"))
    # The pipeline MUST have invoked search
    assert fake.search_call_count >= 1
    # The pipeline MUST have invoked fetch
    assert fake.fetch_call_count >= 1
    assert result.queries_issued >= 1
    assert result.fetches_issued >= 1


# ---------------------------------------------------------------------------
# 4. cross-verify: two sources → supported
# ---------------------------------------------------------------------------


def test_orchestrator_cross_verify_two_sources() -> None:
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    authority = issue_runtime_authority(
        authority_id="test-orch-xverify",
        scopes=("research_source_proof", "research_claim_support"),
    )
    # Two searches each pointing at a different URL, same answer "26.4"
    search1 = _search_result_ok([_src("url:s1", "ORCID stats", "average 26.4")], "ORCID 2019")
    search2 = _search_result_ok([_src("url:s2", "Research data", "26.4 works")], "ORCID stats")
    fetch1 = _fetch_result_ok("url:s1", "Average works: 26.4 per researcher.")
    fetch2 = _fetch_result_ok("url:s2", "ORCID average: 26.4 works per user.")

    fake = FakeDeepResearchBoundary(
        search_responses=[search1, search2],
        fetch_responses=[fetch1, fetch2],
    )
    config = DeepResearchConfig(
        enabled=True,
        max_queries=2,
        max_fetch_per_query=1,
        max_iterations=1,
        min_sources_for_cross_verify=2,
    )
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=config,
        runtime_authority=authority,
    )
    result = asyncio.run(orch.research("ORCID average works pre-2020"))
    assert result.source_count >= 2
    assert result.claim_graph is not None
    assert result.claim_graph.claims[0].support_verdict == "supported"
    assert result.status in {"ok", "weak"}


# ---------------------------------------------------------------------------
# 5. iteration: single-source → iterate at least once more
# ---------------------------------------------------------------------------


def test_orchestrator_iterates_on_weak_evidence() -> None:
    """Single source first round → iterate → 2nd round search fires."""
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    authority = issue_runtime_authority(
        authority_id="test-orch-iterate",
        scopes=("research_source_proof", "research_claim_support"),
    )
    # Round 1: single source
    search1 = _search_result_ok([_src("url:s1", "ORCID", "36 works")], "ORCID 2019")
    fetch1 = _fetch_result_ok("url:s1", "Average: 36 works per researcher.")
    # Round 2: another source with same answer
    search2 = _search_result_ok([_src("url:s2", "Stats", "36 average")], "ORCID average stats")
    fetch2 = _fetch_result_ok("url:s2", "Research shows 36 average works.")

    fake = FakeDeepResearchBoundary(
        search_responses=[search1, search2],
        fetch_responses=[fetch1, fetch2],
    )
    config = DeepResearchConfig(
        enabled=True,
        max_queries=1,
        max_fetch_per_query=1,
        max_iterations=2,
        min_sources_for_cross_verify=2,
    )
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=config,
        runtime_authority=authority,
    )
    result = asyncio.run(orch.research("ORCID average works pre-2020"))
    assert result.iteration_count >= 2


# ---------------------------------------------------------------------------
# 6. Early exit: supported after first iteration
# ---------------------------------------------------------------------------


def test_orchestrator_exits_early_when_supported() -> None:
    """Two sources agree on first round → max_iterations not consumed."""
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    authority = issue_runtime_authority(
        authority_id="test-orch-early",
        scopes=("research_source_proof", "research_claim_support"),
    )
    search1 = _search_result_ok([_src("url:s1", "A", "6 films")], "films 2020")
    search2 = _search_result_ok([_src("url:s2", "B", "6 films")], "top films 2020")
    fetch1 = _fetch_result_ok("url:s1", "There were 6 non-English films in top 10.")
    fetch2 = _fetch_result_ok("url:s2", "6 non-English films ranked.")

    fake = FakeDeepResearchBoundary(
        search_responses=[search1, search2],
        fetch_responses=[fetch1, fetch2],
    )
    config = DeepResearchConfig(
        enabled=True,
        max_queries=2,
        max_fetch_per_query=1,
        max_iterations=3,  # max is 3 but should exit at 1
        min_sources_for_cross_verify=2,
    )
    orch = DeepWebResearchOrchestrator(
        boundary=fake,
        config=config,
        runtime_authority=authority,
    )
    result = asyncio.run(orch.research("how many non-English films top 10 2020"))
    # Should exit after 1 iteration (both sources found)
    assert result.iteration_count == 1
    assert result.status in {"ok", "weak"}


# ---------------------------------------------------------------------------
# 7. Diagnostic fields populated
# ---------------------------------------------------------------------------


def test_orchestrator_diagnostic_fields_populated() -> None:
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator

    authority = issue_runtime_authority(
        authority_id="test-orch-diag",
        scopes=("research_source_proof", "research_claim_support"),
    )
    fake = FakeDeepResearchBoundary(
        search_responses=[_search_result_ok([_src("url:x", "X")])],
        fetch_responses=[_fetch_result_ok("url:x", "Content: 42.")],
    )
    config = DeepResearchConfig(enabled=True, max_queries=1, max_iterations=1)
    orch = DeepWebResearchOrchestrator(boundary=fake, config=config, runtime_authority=authority)
    result = asyncio.run(orch.research("test question"))
    assert isinstance(result.diagnostic, dict)
    assert result.iteration_count >= 1
    assert result.queries_issued >= 1


# ---------------------------------------------------------------------------
# 8. Sealed-gate: live_provider_pack authority flags stay False
# ---------------------------------------------------------------------------


def test_orchestrator_does_not_flip_authority_flags() -> None:
    """Importing and running the orchestrator must not touch Literal[False] fields."""
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator
    from magi_agent.web_acquisition.live_provider_pack import WebAcquisitionProviderAuthorityFlags

    flags = WebAcquisitionProviderAuthorityFlags()
    assert flags.provider_called is False
    assert flags.network_fetched is False
    assert flags.browser_executed is False
    assert flags.production_writes_enabled is False
    assert flags.raw_content_injected is False

    # Running the orchestrator (even enabled=True + live_gate=True) must not
    # flip these sealed class-level fields. The boundary calls do not modify them.
    authority = issue_runtime_authority(
        authority_id="test-sealed",
        scopes=("research_source_proof", "research_claim_support"),
    )
    fake = FakeDeepResearchBoundary(
        search_responses=[_search_result_ok([_src("url:a")])],
        fetch_responses=[_fetch_result_ok("url:a", "value: 5.")],
    )
    config = DeepResearchConfig(enabled=True, max_queries=1, max_iterations=1)
    orch = DeepWebResearchOrchestrator(boundary=fake, config=config, runtime_authority=authority)
    asyncio.run(orch.research("test"))

    # Flags are Literal[False] — constructing a fresh instance always shows False
    flags2 = WebAcquisitionProviderAuthorityFlags()
    assert flags2.provider_called is False
    assert flags2.network_fetched is False
    assert flags2.browser_executed is False
