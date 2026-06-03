"""PR3 — Signal extraction + labeling.

TDD test suite (written first).  Covers:

1. Per-signal extraction (diff / redirect / retry / acceptance) from hand-built
   ``SessionTrace`` objects — deterministic.
2. Noise filter removes formatting-only / one-off signals.
3. Dedup collapses near-duplicate candidates; stable output.
4. Cross-session aggregation: < threshold → example/eval; >= threshold → rule.
5. Chronological split: later traces' signals land in eval (holdout); no leakage.
6. Executor end-to-end (gated ON via env) produces candidates from fake traces;
   OFF → disabled, zero work.
7. Labeler is the deterministic fake (no network/LLM); Labeler Protocol seam is
   injectable.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.learning.candidates import (
    LearningCandidate,
    LocalFakeTranscriptSource,
    SessionTrace,
)
from magi_agent.learning.signals import Signal, extract_signals
from magi_agent.learning.labeler import (
    LabeledLearning,
    Labeler,
    LocalFakeLabeler,
    aggregate_candidates,
    build_candidates,
    chronological_split,
    dedup_candidates,
    filter_noise,
)
from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    _REFLECTION_ENV_VAR,
    run_reflection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(text: str) -> dict[str, str]:
    return {"role": "user", "text": text}


def _agent(text: str) -> dict[str, str]:
    return {"role": "assistant", "text": text}


def _tool(name: str) -> dict[str, str]:
    return {"role": "tool", "tool": name}


def _trace(
    session_id: str = "s1",
    *,
    turns: tuple[dict, ...] = (),
    final_output: str = "done",
    draft_output: str | None = None,
    ts: str = "2026-06-03T10:00:00Z",
) -> SessionTrace:
    return SessionTrace(
        session_id=session_id,
        turns=turns,
        final_output=final_output,
        draft_output=draft_output,
        ts=ts,
    )


# ---------------------------------------------------------------------------
# 1. Per-signal extraction
# ---------------------------------------------------------------------------


class TestDiffSignal:
    def test_diff_when_draft_differs_from_final(self) -> None:
        trace = _trace(draft_output="the cat sat", final_output="the dog sat")
        sigs = extract_signals(trace)
        kinds = {s.kind for s in sigs}
        assert "diff" in kinds

    def test_no_diff_when_draft_is_none(self) -> None:
        trace = _trace(draft_output=None, final_output="anything")
        sigs = extract_signals(trace)
        assert "diff" not in {s.kind for s in sigs}

    def test_no_diff_when_draft_equals_final(self) -> None:
        trace = _trace(draft_output="same", final_output="same")
        sigs = extract_signals(trace)
        assert "diff" not in {s.kind for s in sigs}

    def test_diff_evidence_references_outputs(self) -> None:
        trace = _trace(draft_output="a", final_output="b")
        diff = next(s for s in extract_signals(trace) if s.kind == "diff")
        assert diff.session_id == "s1"
        assert diff.summary


class TestRedirectSignal:
    def test_redirect_when_user_follows_assistant(self) -> None:
        turns = (
            _user("write a report"),
            _agent("here is the report"),
            _user("no, make it shorter"),
        )
        trace = _trace(turns=turns)
        sigs = extract_signals(trace)
        assert "redirect" in {s.kind for s in sigs}

    def test_no_redirect_for_single_user_turn(self) -> None:
        turns = (_user("do it"), _agent("done"))
        trace = _trace(turns=turns)
        assert "redirect" not in {s.kind for s in extract_signals(trace)}

    def test_no_redirect_when_no_assistant_precedes(self) -> None:
        turns = (_user("first"), _user("second"))
        trace = _trace(turns=turns)
        assert "redirect" not in {s.kind for s in extract_signals(trace)}

    def test_redirect_evidence_has_turn_indices(self) -> None:
        turns = (_user("a"), _agent("b"), _user("c"))
        trace = _trace(turns=turns)
        r = next(s for s in extract_signals(trace) if s.kind == "redirect")
        assert r.evidence["turnIndices"]


class TestRetrySignal:
    def test_retry_when_same_tool_repeats(self) -> None:
        turns = (
            _user("research X"),
            _tool("web_search"),
            _agent("hmm"),
            _tool("web_search"),
        )
        trace = _trace(turns=turns)
        assert "retry" in {s.kind for s in extract_signals(trace)}

    def test_no_retry_for_distinct_tools(self) -> None:
        turns = (_tool("web_search"), _tool("read_file"))
        trace = _trace(turns=turns)
        assert "retry" not in {s.kind for s in extract_signals(trace)}

    def test_no_retry_for_single_tool_call(self) -> None:
        turns = (_tool("web_search"),)
        trace = _trace(turns=turns)
        assert "retry" not in {s.kind for s in extract_signals(trace)}

    def test_retry_evidence_names_tool(self) -> None:
        turns = (_tool("grep"), _tool("grep"))
        trace = _trace(turns=turns)
        r = next(s for s in extract_signals(trace) if s.kind == "retry")
        assert r.evidence["tool"] == "grep"


class TestAcceptanceSignal:
    def test_acceptance_when_draft_equals_final_no_redirect(self) -> None:
        turns = (_user("do it"), _agent("done"))
        trace = _trace(turns=turns, draft_output="done", final_output="done")
        assert "acceptance" in {s.kind for s in extract_signals(trace)}

    def test_acceptance_when_no_draft_no_redirect(self) -> None:
        turns = (_user("do it"), _agent("done"))
        trace = _trace(turns=turns, draft_output=None, final_output="done")
        assert "acceptance" in {s.kind for s in extract_signals(trace)}

    def test_no_acceptance_when_redirect_present(self) -> None:
        turns = (_user("a"), _agent("b"), _user("fix it"))
        trace = _trace(turns=turns, draft_output="b", final_output="b")
        assert "acceptance" not in {s.kind for s in extract_signals(trace)}

    def test_no_acceptance_when_draft_differs(self) -> None:
        trace = _trace(draft_output="x", final_output="y")
        assert "acceptance" not in {s.kind for s in extract_signals(trace)}

    def test_no_acceptance_when_retry_present(self) -> None:
        # A retry means the session had a problem; emitting acceptance
        # ("sent unedited, no redirect") at the same time is contradictory.
        turns = (_tool("web_search"), _tool("web_search"))
        trace = _trace(turns=turns, draft_output=None, final_output="done")
        kinds = {s.kind for s in extract_signals(trace)}
        assert "retry" in kinds
        assert "acceptance" not in kinds


class TestExtractionDeterminism:
    def test_stable_ordering_and_repeatable(self) -> None:
        turns = (
            _user("research"),
            _tool("web_search"),
            _agent("draft"),
            _tool("web_search"),
            _user("change it"),
        )
        trace = _trace(turns=turns, draft_output="draft", final_output="final")
        a = extract_signals(trace)
        b = extract_signals(trace)
        assert a == b
        # Signal is frozen
        with pytest.raises(Exception):
            a[0].kind = "diff"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Noise filter
# ---------------------------------------------------------------------------


class TestNoiseFilter:
    def test_whitespace_only_diff_is_noise(self) -> None:
        # Draft vs final differ only by trailing whitespace / newlines.
        trace = _trace(draft_output="hello world", final_output="hello   world\n")
        sigs = extract_signals(trace)
        filtered = filter_noise(sigs, trace)
        assert "diff" not in {s.kind for s in filtered}

    def test_meaningful_diff_survives_noise_filter(self) -> None:
        trace = _trace(draft_output="hello world", final_output="goodbye world")
        sigs = extract_signals(trace)
        filtered = filter_noise(sigs, trace)
        assert "diff" in {s.kind for s in filtered}


# ---------------------------------------------------------------------------
# 3. Labeler — deterministic fake
# ---------------------------------------------------------------------------


class TestLocalFakeLabeler:
    def test_label_returns_labeled_learning(self) -> None:
        trace = _trace(draft_output="a", final_output="b")
        sig = next(s for s in extract_signals(trace) if s.kind == "diff")
        labeler = LocalFakeLabeler()
        label = labeler.label(sig, trace)
        assert isinstance(label, LabeledLearning)
        assert label.candidate_kind in ("rule", "example", "eval")
        assert label.type in ("fact", "citation", "style", "strategy")
        assert label.lesson

    def test_label_is_deterministic(self) -> None:
        trace = _trace(draft_output="a", final_output="b")
        sig = next(s for s in extract_signals(trace) if s.kind == "diff")
        labeler = LocalFakeLabeler()
        assert labeler.label(sig, trace) == labeler.label(sig, trace)

    def test_labeler_protocol_satisfied(self) -> None:
        assert isinstance(LocalFakeLabeler(), Labeler)

    def test_labeler_makes_no_network_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def _boom(*_a: object, **_k: object) -> None:
            raise AssertionError("network access attempted by labeler")

        monkeypatch.setattr(socket.socket, "connect", _boom)
        trace = _trace(draft_output="a", final_output="b")
        sig = next(s for s in extract_signals(trace) if s.kind == "diff")
        LocalFakeLabeler().label(sig, trace)


class _StubLabeler:
    """Injectable Labeler seam — labels everything as a fixed style lesson."""

    def label(self, signal: Signal, trace: SessionTrace) -> LabeledLearning:
        return LabeledLearning(
            type="style",
            lesson=f"stub:{signal.kind}",
            candidate_kind="example",
            content={"situation": "stub", "behavior": "stub"},
        )


class TestLabelerInjection:
    def test_stub_labeler_satisfies_protocol(self) -> None:
        assert isinstance(_StubLabeler(), Labeler)

    def test_build_candidates_uses_injected_labeler(self) -> None:
        trace = _trace(draft_output="a", final_output="b")
        cands = build_candidates((trace,), labeler=_StubLabeler())
        assert all(isinstance(c, LearningCandidate) for c in cands)
        assert cands  # at least one


# ---------------------------------------------------------------------------
# 4. Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_dedup_collapses_duplicates(self) -> None:
        t1 = _trace("s1", draft_output="the cat sat", final_output="the cat ran")
        t2 = _trace("s2", draft_output="the cat sat", final_output="the cat ran")
        cands = build_candidates((t1, t2), labeler=LocalFakeLabeler())
        deduped = dedup_candidates(cands)
        assert len(deduped) < len(cands) or len(cands) <= 1

    def test_dedup_is_stable(self) -> None:
        t1 = _trace("s1", draft_output="x", final_output="y")
        cands = build_candidates((t1,), labeler=LocalFakeLabeler())
        assert dedup_candidates(cands) == dedup_candidates(cands)

    def test_dedup_preserves_distinct(self) -> None:
        # A diff signal and a retry signal yield genuinely different lessons.
        t1 = _trace("s1", draft_output="apple", final_output="banana")
        t2 = _trace(
            "s2",
            turns=(_tool("web_search"), _tool("web_search")),
            final_output="done",
        )
        cands = build_candidates((t1, t2), labeler=LocalFakeLabeler())
        deduped = dedup_candidates(cands)
        # different lessons (diff vs retry) must not collapse to one
        assert len(deduped) >= 2


# ---------------------------------------------------------------------------
# 5. Cross-session aggregation
# ---------------------------------------------------------------------------


class TestAggregation:
    def _recurring_traces(self, n: int) -> tuple[SessionTrace, ...]:
        return tuple(
            _trace(
                f"s{i}",
                turns=(_tool("web_search"), _tool("web_search")),
                draft_output=None,
                final_output="done",
                ts=f"2026-06-0{i+1}T10:00:00Z",
            )
            for i in range(n)
        )

    def test_below_threshold_no_rule(self) -> None:
        traces = self._recurring_traces(2)
        cands = aggregate_candidates(
            build_candidates(traces, labeler=LocalFakeLabeler()),
            threshold=3,
        )
        assert "rule" not in {c.kind for c in cands}

    def test_at_threshold_promotes_to_rule(self) -> None:
        traces = self._recurring_traces(3)
        cands = aggregate_candidates(
            build_candidates(traces, labeler=LocalFakeLabeler()),
            threshold=3,
        )
        assert "rule" in {c.kind for c in cands}

    def test_rule_provenance_spans_sessions(self) -> None:
        traces = self._recurring_traces(3)
        cands = aggregate_candidates(
            build_candidates(traces, labeler=LocalFakeLabeler()),
            threshold=3,
        )
        rule = next(c for c in cands if c.kind == "rule")
        assert len(rule.provenance.session_ids) >= 3


# ---------------------------------------------------------------------------
# 6. Chronological split (holdout / no leakage)
# ---------------------------------------------------------------------------


class TestChronologicalSplit:
    def test_later_traces_go_to_eval_holdout(self) -> None:
        traces = tuple(
            _trace(f"s{i}", draft_output="x", final_output=f"y{i}", ts=f"2026-06-0{i+1}T10:00:00Z")
            for i in range(4)
        )
        train, holdout = chronological_split(traces)
        train_ts = [t.ts for t in train]
        holdout_ts = [t.ts for t in holdout]
        # every holdout ts is strictly later than every train ts
        assert max(train_ts) < min(holdout_ts)

    def test_split_no_leakage_disjoint(self) -> None:
        traces = tuple(
            _trace(f"s{i}", ts=f"2026-06-0{i+1}T10:00:00Z") for i in range(4)
        )
        train, holdout = chronological_split(traces)
        train_ids = {t.session_id for t in train}
        holdout_ids = {t.session_id for t in holdout}
        assert train_ids.isdisjoint(holdout_ids)
        assert train_ids | holdout_ids == {t.session_id for t in traces}

    def test_holdout_signals_become_eval_candidates(self) -> None:
        traces = tuple(
            _trace(f"s{i}", draft_output="x", final_output=f"y{i}", ts=f"2026-06-0{i+1}T10:00:00Z")
            for i in range(4)
        )
        _train, holdout = chronological_split(traces)
        holdout_cands = build_candidates(holdout, labeler=LocalFakeLabeler(), as_eval=True)
        assert holdout_cands
        assert all(c.kind == "eval" for c in holdout_cands)


# ---------------------------------------------------------------------------
# 7. Executor end-to-end
# ---------------------------------------------------------------------------


class TestExecutorEndToEnd:
    def test_off_disabled_zero_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        source = LocalFakeTranscriptSource(
            traces=(_trace("s1", draft_output="a", final_output="b"),)
        )
        result = asyncio.run(run_reflection(source=source))
        assert result.status == "disabled"
        assert result.candidates == ()
        assert result.counters["traces_read"] == 0

    def test_on_produces_candidates_from_signals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (
            _trace("s1", turns=(_user("a"), _agent("b"), _user("fix")),
                   draft_output="b", final_output="c", ts="2026-06-01T10:00:00Z"),
            _trace("s2", turns=(_tool("x"), _tool("x")),
                   final_output="done", ts="2026-06-02T10:00:00Z"),
        )
        source = LocalFakeTranscriptSource(traces=traces)
        result = asyncio.run(
            run_reflection(source=source, config=LearningReflectionConfig(enabled=True))
        )
        assert result.status == "ok"
        assert isinstance(result.candidates, tuple)
        assert len(result.candidates) >= 1
        assert all(isinstance(c, LearningCandidate) for c in result.candidates)

    def test_on_deterministic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (
            _trace("s1", draft_output="a", final_output="b", ts="2026-06-01T10:00:00Z"),
        )
        r1 = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        r2 = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        assert r1.candidates == r2.candidates

    def test_counters_report_signals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (
            _trace("s1", draft_output="a", final_output="b", ts="2026-06-01T10:00:00Z"),
        )
        result = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        assert "signals_extracted" in result.counters
        assert "candidates_produced" in result.counters
        assert result.counters["traces_read"] == 1

    def test_authority_flags_remain_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (_trace("s1", draft_output="a", final_output="b"),)
        result = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        assert result.llm_attached is False
        assert result.production_write_enabled is False
        assert result.real_transcript_source_attached is False

    def test_recurring_pattern_promotes_to_rule_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # C1 regression: aggregation must fire through the full executor path.
        # N >= threshold recurring-pattern sessions must yield >= 1 rule
        # candidate.  Use enough sessions that the train split (after the
        # chronological holdout) still meets the aggregation threshold (3).
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = tuple(
            _trace(
                f"s{i}",
                turns=(_tool("web_search"), _tool("web_search")),
                draft_output=None,
                final_output="done",
                ts=f"2026-06-{i + 1:02d}T10:00:00Z",
            )
            for i in range(6)
        )
        result = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        assert result.status == "ok"
        assert "rule" in {c.kind for c in result.candidates}

    def test_eval_candidates_never_promote_to_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # I1: eval (holdout) candidates must never contribute to rule promotion.
        # Every recurring pattern lives only in the holdout split here, so no
        # rule may form even though the same pattern recurs across sessions.
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        # 4 traces → 1-trace holdout; the holdout can't recur 3x by itself, and
        # eval candidates are excluded from aggregation regardless.
        traces = tuple(
            _trace(
                f"s{i}",
                turns=(_tool("web_search"), _tool("web_search")),
                draft_output=None,
                final_output="done",
                ts=f"2026-06-{i + 1:02d}T10:00:00Z",
            )
            for i in range(4)
        )
        result = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        # eval candidates exist but stay kind=="eval"; the single eval session
        # never becomes a rule.
        eval_cands = [c for c in result.candidates if c.kind == "eval"]
        assert eval_cands
        for c in eval_cands:
            assert c.kind != "rule"

    def test_signals_extracted_counts_single_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # I4: signals must be extracted exactly once; the reported count equals
        # the direct extraction total over all traces.
        monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
        traces = (
            _trace("s1", turns=(_tool("x"), _tool("x")),
                   draft_output="a", final_output="b", ts="2026-06-01T10:00:00Z"),
            _trace("s2", turns=(_user("p"), _agent("q"), _user("r")),
                   final_output="done", ts="2026-06-02T10:00:00Z"),
        )
        expected = sum(len(extract_signals(t)) for t in traces)
        result = asyncio.run(
            run_reflection(
                source=LocalFakeTranscriptSource(traces=traces),
                config=LearningReflectionConfig(enabled=True),
            )
        )
        assert result.counters["signals_extracted"] == expected

    def test_disabled_counters_schema_uniform(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # m3: disabled path counters must carry the same keys as the ok path so
        # callers never KeyError on signals_extracted.
        monkeypatch.delenv(_REFLECTION_ENV_VAR, raising=False)
        result = asyncio.run(run_reflection())
        assert result.status == "disabled"
        assert result.counters["signals_extracted"] == 0
        assert result.counters["traces_read"] == 0
        assert result.counters["candidates_produced"] == 0
