"""Deep web research orchestrator.

Implements a multi-query → fetch → navigate → cross-verify → iterate loop
via the existing ``LocalWebResearchToolBoundary`` seam.  All provider calls
go through the boundary, so SSRF firewall and authority-flag sealing are
automatically applied.

Default-OFF: ``DeepResearchConfig(enabled=False)`` is the default; the
orchestrator returns ``DeepResearchResult(status="disabled")`` immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from magi_agent.evidence.runtime_issuance import RuntimeIssueAuthority
from magi_agent.research.claim_graph import ResearchClaimGraph
from magi_agent.research.source_proof import ResearchSourceProofVerdict
from magi_agent.web_acquisition.cross_verifier import CrossVerifier, CrossVerifyResult
from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig
from magi_agent.web_acquisition.page_navigator import ExtractedFact, FactExtractor, PageNavigator
from magi_agent.web_acquisition.query_planner import QueryPlanner, SearchQuery
from magi_agent.web_acquisition.research_tools import (
    live_web_acquisition_active,
)

if TYPE_CHECKING:
    from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary

DeepResearchStatus = Literal["ok", "weak", "insufficient", "disabled", "error"]


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeepResearchResult:
    """The outcome of a deep web research run."""

    status: DeepResearchStatus
    answer_candidates: tuple[str, ...]       # Candidates in descending confidence
    source_count: int                        # Independent sources agreeing on top candidate
    claim_graph: ResearchClaimGraph | None   # Populated when supported or weak
    source_verdicts: tuple[object, ...]      # ResearchSourceProofVerdict objects
    iteration_count: int
    queries_issued: int
    fetches_issued: int
    diagnostic: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# URL scoring helpers
# ---------------------------------------------------------------------------

_HIGH_AUTHORITY_DOMAINS = frozenset(
    {
        "boxofficemojo.com",
        "orcid.org",
        "wikipedia.org",
        "en.wikipedia.org",
        "google.com",
        "imdb.com",
        "statista.com",
        "worldometers.info",
    }
)


def _score_source(
    source: dict[str, object],
    question: str,
) -> float:
    """Heuristic relevance score for a search result source."""
    score = 0.0
    title = str(source.get("title") or "").lower()
    snippet = str(source.get("snippet") or "").lower()
    url_ref = str(source.get("urlRef") or "")
    q_lower = question.lower()

    # Keyword overlap with question
    q_words = set(q_lower.split())
    title_words = set(title.split())
    snippet_words = set(snippet.split())
    title_overlap = len(q_words & title_words) / max(len(q_words), 1)
    snippet_overlap = len(q_words & snippet_words) / max(len(q_words), 1)
    score += 0.5 * title_overlap + 0.3 * snippet_overlap

    # Authority domain bonus (raw URLs only; url:<digest> refs get 0)
    if url_ref.startswith(("http://", "https://")):
        try:
            host = (urlsplit(url_ref).hostname or "").casefold()
            for dom in _HIGH_AUTHORITY_DOMAINS:
                if dom in host:
                    score += 0.3
                    break
        except Exception:
            pass

    # Numeric/date presence bonus in snippet
    import re
    if re.search(r"\b\d+\.?\d*\b", snippet):
        score += 0.1

    return score


def _select_urls(
    sources: list[dict[str, object]],
    question: str,
    *,
    max_urls: int,
    already_fetched: set[str],
) -> list[str]:
    """Select top-N URLs from search results, excluding already-fetched ones."""
    scored: list[tuple[float, str]] = []
    for source in sources:
        url_ref = str(source.get("urlRef") or "")
        if not url_ref or url_ref in already_fetched:
            continue
        score = _score_source(source, question)
        scored.append((score, url_ref))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url in scored[:max_urls]]


# ---------------------------------------------------------------------------
# DeepWebResearchOrchestrator
# ---------------------------------------------------------------------------


class DeepWebResearchOrchestrator:
    """Orchestrates the multi-query / fetch / navigate / cross-verify research loop.

    Parameters
    ----------
    boundary:
        The ``LocalWebResearchToolBoundary`` to dispatch all tool calls through.
        The boundary owns SSRF filtering and authority-flag enforcement.
    config:
        ``DeepResearchConfig`` with ``enabled=False`` default.
    runtime_authority:
        Optional runtime-issue authority for source proof and claim graph
        operations.  When ``None``, cross-verification is skipped.
    """

    def __init__(
        self,
        *,
        boundary: "LocalWebResearchToolBoundary",
        config: DeepResearchConfig,
        runtime_authority: RuntimeIssueAuthority | None = None,
    ) -> None:
        self._boundary = boundary
        self._config = config
        self._runtime_authority = runtime_authority
        self._planner = QueryPlanner()
        self._navigator = PageNavigator()
        self._extractor = FactExtractor()
        self._verifier = CrossVerifier()

    async def research(
        self,
        question: str,
        *,
        context: object | None = None,
    ) -> DeepResearchResult:
        """Run the deep research loop for *question*.

        Returns immediately with ``status="disabled"`` when the config gate
        is off or the live acquisition gate is inactive.
        """
        # Gate 1: config disabled
        if not self._config.enabled:
            return _disabled_result()

        # Gate 2: live acquisition gate
        boundary_env = getattr(self._boundary, "_env", None)
        if not live_web_acquisition_active(env=boundary_env):
            return _disabled_result()

        return await self._run_loop(question, context=context)

    async def _run_loop(
        self,
        question: str,
        *,
        context: object | None,
    ) -> DeepResearchResult:
        config = self._config
        all_facts: list[ExtractedFact] = []
        already_fetched: set[str] = set()
        queries_issued = 0
        fetches_issued = 0
        iteration_count = 0
        cv_result: CrossVerifyResult | None = None

        for iteration in range(config.max_iterations):
            iteration_count += 1
            # --- PLAN ---
            planned_queries = self._planner.plan(question, config)

            # On subsequent iterations, shift query emphasis to new angles
            if iteration > 0:
                planned_queries = _refine_queries_for_iteration(
                    planned_queries,
                    iteration=iteration,
                    already_fetched=already_fetched,
                    config=config,
                )

            # --- SEARCH ---
            iter_sources: list[dict[str, object]] = []
            for query in planned_queries:
                result = await self._boundary.execute_tool(
                    "WebSearch",
                    {"query": query.text},
                    context,
                )
                queries_issued += 1
                if result.status == "ok" and isinstance(result.output, dict):
                    sources = result.output.get("sources") or []
                    if isinstance(sources, list):
                        iter_sources.extend(sources)

            # --- SELECT & FETCH ---
            urls = _select_urls(
                iter_sources,
                question,
                max_urls=config.max_fetch_per_query * len(planned_queries),
                already_fetched=already_fetched,
            )

            for url_ref in urls:
                fetch_result = await self._boundary.execute_tool(
                    "WebFetch",
                    {"url": url_ref},
                    context,
                )
                fetches_issued += 1
                already_fetched.add(url_ref)

                if fetch_result.status != "ok" or not isinstance(fetch_result.output, dict):
                    continue

                # --- NAVIGATE & EXTRACT ---
                page_text = str(fetch_result.output.get("publicPreview") or "")
                if not page_text:
                    continue

                if config.navigate_sections:
                    section = self._navigator.extract_target(page_text, question)
                else:
                    from magi_agent.web_acquisition.page_navigator import ExtractedSection
                    section = ExtractedSection(content=page_text, section_kind="full")

                span_prefix = f"iter{iteration}.url{len(already_fetched)}"
                extracted = self._extractor.extract(
                    section,
                    source_url_ref=url_ref,
                    span_ref_prefix=span_prefix,
                )
                all_facts.extend(extracted)

            # --- CROSS-VERIFY ---
            if self._runtime_authority is not None and all_facts:
                cv_result = self._verifier.verify(
                    all_facts,
                    config,
                    self._runtime_authority,
                )
                # Exit early if we have sufficient evidence
                if cv_result.verdict == "supported":
                    break
                if cv_result.source_count >= config.min_sources_for_cross_verify:
                    break
            elif all_facts:
                # No runtime_authority — can't build formal proof; treat as weak
                cv_result = None

        # --- JUDGE final status ---
        status, answer_candidates = _judge_final(cv_result, all_facts, config)

        return DeepResearchResult(
            status=status,
            answer_candidates=answer_candidates,
            source_count=cv_result.source_count if cv_result is not None else 0,
            claim_graph=cv_result.claim_graph if cv_result is not None else None,
            source_verdicts=cv_result.source_verdicts if cv_result is not None else (),
            iteration_count=iteration_count,
            queries_issued=queries_issued,
            fetches_issued=fetches_issued,
            diagnostic=_build_diagnostic(cv_result, all_facts, queries_issued, fetches_issued),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _disabled_result() -> DeepResearchResult:
    return DeepResearchResult(
        status="disabled",
        answer_candidates=(),
        source_count=0,
        claim_graph=None,
        source_verdicts=(),
        iteration_count=0,
        queries_issued=0,
        fetches_issued=0,
        diagnostic={"reason": "disabled"},
    )


def _judge_final(
    cv_result: CrossVerifyResult | None,
    all_facts: list[ExtractedFact],
    config: DeepResearchConfig,
) -> tuple[DeepResearchStatus, tuple[str, ...]]:
    if cv_result is None:
        if not all_facts:
            return "insufficient", ()
        # Facts present but no runtime_authority — report as weak
        top_vals = sorted(
            {f.value for f in all_facts},
            key=lambda v: sum(1 for f in all_facts if f.value == v),
            reverse=True,
        )
        return "weak", tuple(top_vals[:3])

    verdict = cv_result.verdict
    if verdict == "supported":
        candidates = (cv_result.top_candidate,) if cv_result.top_candidate else ()
        return "ok", candidates
    if verdict in {"weak", "insufficient"}:
        candidates = (cv_result.top_candidate,) if cv_result.top_candidate else ()
        return "weak", candidates
    if verdict == "contradicted":
        candidates = (cv_result.top_candidate,) if cv_result.top_candidate else ()
        return "weak", candidates
    # not_evaluated
    return "insufficient", ()


def _refine_queries_for_iteration(
    original_queries: list[SearchQuery],
    *,
    iteration: int,
    already_fetched: set[str],
    config: DeepResearchConfig,
) -> list[SearchQuery]:
    """Add iteration-specific query variants (more targeted on 2nd round)."""
    from magi_agent.web_acquisition.query_planner import SearchQuery as SQ

    if not original_queries:
        return original_queries

    base_text = original_queries[0].text
    # Add "official statistics" / "data report" angle to broaden coverage
    extras = [
        SQ(text=f"{base_text} official statistics", variant_kind="iteration_stats"),
        SQ(text=f"{base_text} data report", variant_kind="iteration_data"),
    ]
    combined = list(original_queries) + extras
    # Cap at max_queries
    return combined[: config.max_queries]


def _build_diagnostic(
    cv_result: CrossVerifyResult | None,
    all_facts: list[ExtractedFact],
    queries_issued: int,
    fetches_issued: int,
) -> dict[str, object]:
    diag: dict[str, object] = {
        "total_facts_extracted": len(all_facts),
        "queries_issued": queries_issued,
        "fetches_issued": fetches_issued,
    }
    if cv_result is not None:
        diag.update(cv_result.diagnostic)
    return diag


__all__ = [
    "DeepResearchResult",
    "DeepResearchStatus",
    "DeepWebResearchOrchestrator",
]
