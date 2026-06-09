"""Tests for the PR3 egress critic gate.

Fake-model only (NO real LLM). The gate runs a fact-critical classifier first,
then (only when fact-critical) a lean grounding critic. Fail-open everywhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.introspection.egress_gate import (
    EGRESS_CRITIC_EVIDENCE_TYPE,
    run_egress_critic_check,
)
from magi_agent.introspection.projection import (
    FileReadView,
    SessionEvidenceView,
    SessionScopeView,
    ToolCallView,
)


# ---------------------------------------------------------------------------
# Fake ADK model helpers
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)


def _llm(json_text: str, *, counter: list[int] | None = None):
    def _factory() -> object:
        class _FakeLlm:
            model = "fake-critic-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                if counter is not None:
                    counter.append(1)
                yield _FakeLlmResponse(json_text)

        return _FakeLlm()

    return _factory


def _error_llm():
    def _factory() -> object:
        class _ErrorLlm:
            model = "fake-critic-model"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                raise RuntimeError("boom")
                yield  # pragma: no cover

        return _ErrorLlm()

    return _factory


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _empty_view() -> SessionEvidenceView:
    return SessionEvidenceView(scope=SessionScopeView(sessionId="s-1", turnsCovered=()))


def _view_with_evidence() -> SessionEvidenceView:
    return SessionEvidenceView(
        scope=SessionScopeView(sessionId="s-1", turnsCovered=("turn-1",)),
        filesRead=(
            FileReadView(path="report.pdf", sha256="sha256:" + "a" * 64, turnId="turn-1", bytes=10),
        ),
        toolCalls=(ToolCallView(name="FileRead", status="ok", turnId="turn-1"),),
    )


# ---------------------------------------------------------------------------
# Not fact-critical -> no critic call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_fact_critical_no_evidence_skips_critic() -> None:
    critic_calls: list[int] = []
    result = await run_egress_critic_check(
        draft_text="some answer",
        user_query="hello",
        view=_empty_view(),  # no evidence activity -> not fact-critical
        model_factory=_llm('{"grounded": true, "relevant": true}', counter=critic_calls),
    )
    assert result.status is None
    assert result.fact_critical is False
    assert result.critic_invoked is False
    assert critic_calls == []  # critic NEVER called


@pytest.mark.asyncio
async def test_not_fact_critical_by_classifier_skips_critic() -> None:
    critic_calls: list[int] = []
    result = await run_egress_critic_check(
        draft_text="answer",
        user_query="thanks",
        view=_view_with_evidence(),
        model_factory=_llm('{"grounded": true, "relevant": true}', counter=critic_calls),
        fact_critical_model_factory=_llm('{"fact_critical": false, "reason": "chit-chat"}'),
    )
    assert result.status is None
    assert result.fact_critical is False
    assert result.critic_invoked is False
    assert critic_calls == []


# ---------------------------------------------------------------------------
# Fact-critical -> critic runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_critical_grounded_passes() -> None:
    result = await run_egress_critic_check(
        draft_text="I read report.pdf and it says X.",
        user_query="what did you find in report.pdf?",
        view=_view_with_evidence(),
        model_factory=_llm('{"grounded": true, "relevant": true, "reason": "ok"}'),
        fact_critical_model_factory=_llm('{"fact_critical": true, "reason": "verify"}'),
    )
    assert result.status == "passed"
    assert result.fact_critical is True
    assert result.critic_invoked is True
    assert result.grounded is True
    assert result.relevant is True


@pytest.mark.asyncio
async def test_fact_critical_contradiction_is_missing_evidence() -> None:
    result = await run_egress_critic_check(
        draft_text="I read 5 files and ran the deploy.",
        user_query="what did you do?",
        view=_view_with_evidence(),  # only 1 file, no deploy tool
        model_factory=_llm(
            '{"grounded": false, "relevant": true, "reason": "claims unsupported"}'
        ),
        fact_critical_model_factory=_llm('{"fact_critical": true, "reason": "verify"}'),
    )
    assert result.status == "missing_evidence"
    assert result.fact_critical is True
    assert result.grounded is False
    assert result.source == "ungrounded"


@pytest.mark.asyncio
async def test_fact_critical_irrelevant_is_missing_evidence() -> None:
    result = await run_egress_critic_check(
        draft_text="The weather is nice today.",
        user_query="what did you find in report.pdf?",
        view=_view_with_evidence(),
        model_factory=_llm('{"grounded": true, "relevant": false, "reason": "off-topic"}'),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    assert result.status == "missing_evidence"
    assert result.relevant is False


# ---------------------------------------------------------------------------
# Fail-open: critic error/timeout -> status None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critic_error_fails_open() -> None:
    result = await run_egress_critic_check(
        draft_text="answer",
        user_query="verify this",
        view=_view_with_evidence(),
        model_factory=_error_llm(),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    assert result.status is None  # fail-open, never blocks
    assert result.fact_critical is True
    assert result.critic_invoked is True
    assert result.source == "critic_error"


@pytest.mark.asyncio
async def test_critic_invalid_json_fails_open() -> None:
    result = await run_egress_critic_check(
        draft_text="answer",
        user_query="verify",
        view=_view_with_evidence(),
        model_factory=_llm("totally not json"),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    assert result.status is None
    assert result.source == "critic_error"


@pytest.mark.asyncio
async def test_critic_timeout_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_EGRESS_CRITIC_TIMEOUT", "0.05")

    def _slow_factory() -> object:
        class _SlowLlm:
            model = "slow"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                await asyncio.sleep(10)
                yield _FakeLlmResponse('{"grounded": true, "relevant": true}')

        return _SlowLlm()

    result = await run_egress_critic_check(
        draft_text="answer",
        user_query="verify",
        view=_view_with_evidence(),
        model_factory=_slow_factory,
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    assert result.status is None
    assert "timeout" in result.reason.lower()


@pytest.mark.asyncio
async def test_no_critic_model_fails_open_when_fact_critical() -> None:
    result = await run_egress_critic_check(
        draft_text="answer",
        user_query="verify",
        view=_view_with_evidence(),
        model_factory=None,
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    assert result.status is None
    assert result.fact_critical is True
    assert result.source == "critic_error"


# ---------------------------------------------------------------------------
# Evidence emission
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Input caps / empty-input slicing (pure-slicing; fake model captures prompt)
# ---------------------------------------------------------------------------


def _capturing_llm(captured: dict, json_text: str = '{"grounded": true, "relevant": true}'):
    def _factory() -> object:
        class _CapturingLlm:
            model = "fake-capturing"

            async def generate_content_async(
                self, llm_request: Any, stream: bool = False
            ) -> AsyncGenerator:
                captured["prompt"] = llm_request.contents[0].parts[0].text
                yield _FakeLlmResponse(json_text)

        return _CapturingLlm()

    return _factory


def _big_view(n_files: int) -> SessionEvidenceView:
    return SessionEvidenceView(
        scope=SessionScopeView(sessionId="s-1", turnsCovered=("turn-1",)),
        filesRead=tuple(
            FileReadView(
                path=f"file-{i}.txt",
                sha256="sha256:" + "a" * 64,
                turnId="turn-1",
                bytes=1,
            )
            for i in range(n_files)
        ),
        toolCalls=(ToolCallView(name="FileRead", status="ok", turnId="turn-1"),),
    )


@pytest.mark.asyncio
async def test_oversized_draft_and_query_truncated() -> None:
    from magi_agent.introspection import egress_gate as eg

    captured: dict = {}
    await run_egress_critic_check(
        draft_text="D" * (eg._MAX_DRAFT_CHARS + 5000),
        user_query="Q" * (eg._MAX_QUERY_CHARS + 5000),
        view=_view_with_evidence(),
        model_factory=_capturing_llm(captured),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    # Each injected run is capped exactly at its limit (a run of the limit
    # length is present, one char longer is not).
    assert ("D" * eg._MAX_DRAFT_CHARS) in captured["prompt"]
    assert ("D" * (eg._MAX_DRAFT_CHARS + 1)) not in captured["prompt"]
    assert ("Q" * eg._MAX_QUERY_CHARS) in captured["prompt"]
    assert ("Q" * (eg._MAX_QUERY_CHARS + 1)) not in captured["prompt"]


@pytest.mark.asyncio
async def test_empty_draft_and_query_render_empty_placeholder() -> None:
    captured: dict = {}
    await run_egress_critic_check(
        draft_text="",
        user_query="",
        view=_view_with_evidence(),
        model_factory=_capturing_llm(captured),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    # Both empty draft and empty query collapse to the (empty) placeholder.
    assert captured["prompt"].count("(empty)") == 2


@pytest.mark.asyncio
async def test_view_items_capped_in_rendered_prompt() -> None:
    from magi_agent.introspection import egress_gate as eg

    captured: dict = {}
    await run_egress_critic_check(
        draft_text="answer",
        user_query="what files did you read?",
        view=_big_view(eg._MAX_VIEW_ITEMS + 25),
        model_factory=_capturing_llm(captured),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
    )
    # Only the first _MAX_VIEW_ITEMS files are rendered into the view JSON.
    assert captured["prompt"].count("file-") == eg._MAX_VIEW_ITEMS
    assert "file-0.txt" in captured["prompt"]
    assert f"file-{eg._MAX_VIEW_ITEMS - 1}.txt" in captured["prompt"]
    assert f"file-{eg._MAX_VIEW_ITEMS}.txt" not in captured["prompt"]


def test_render_view_caps_items_directly() -> None:
    """Pure-slicing _render_view: no model needed."""
    from magi_agent.introspection import egress_gate as eg

    payload = eg._render_view(_big_view(eg._MAX_VIEW_ITEMS + 10))
    assert payload.count('"path"') == eg._MAX_VIEW_ITEMS


@pytest.mark.asyncio
async def test_emits_critic_evidence_record() -> None:
    records: list[dict] = []
    await run_egress_critic_check(
        draft_text="grounded answer",
        user_query="verify",
        view=_view_with_evidence(),
        model_factory=_llm('{"grounded": true, "relevant": true, "reason": "ok"}'),
        fact_critical_model_factory=_llm('{"fact_critical": true}'),
        evidence_sink=records.append,
    )
    types = [r["type"] for r in records]
    assert EGRESS_CRITIC_EVIDENCE_TYPE in types
    egress_records = [r for r in records if r["type"] == EGRESS_CRITIC_EVIDENCE_TYPE]
    assert egress_records[-1]["status"] == "passed"
    assert egress_records[-1]["critic_invoked"] is True
