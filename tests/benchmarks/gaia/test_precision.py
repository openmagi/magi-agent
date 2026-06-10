"""Tests for GAIA cross-verified precision pass (PR1 + PR2 + PR3).

Hermetic: all fakes injected — no network, no exec, no real model.

TDD: these tests are written BEFORE the implementation in
benchmarks/gaia/precision.py.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import pytest


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _fake_search_no_conflict(query: str) -> str:
    """Returns evidence that agrees with any reasonable draft."""
    return "The answer is 6 according to official records."


def _fake_search_conflict(query: str) -> str:
    """Returns evidence that disagrees with draft=7, supports 6."""
    return "Confirmed: the count is 6, not 7. (source: official database)"


def _fake_search_unverifiable(query: str) -> str:
    """Returns vague/uncertain text — no extractable value."""
    return "This topic is complicated and sources disagree."


def _fake_fetch_supports_6(url: str) -> str:
    """Returns primary-source page text that clearly states 6."""
    return "According to the official registry, the total count is 6 (six)."


def _raising_search(query: str) -> str:
    raise RuntimeError("network error")


def _model_agree(prompt: str) -> str:
    """Fake model: says the two values AGREE (no conflict)."""
    return "VERDICT: AGREE\nVALUE: 6"


def _model_conflict_adopt_new(prompt: str) -> str:
    """Fake model: says the values CONFLICT; new evidence supports 6."""
    return "VERDICT: CONFLICT\nADOPTED_VALUE: 6\nSOURCE_URL: https://example.com/official"


def _model_unverifiable(prompt: str) -> str:
    """Fake model: evidence is unverifiable / uncertain."""
    return "VERDICT: UNVERIFIABLE"


# ---------------------------------------------------------------------------
# PR1: cross_verify_fact
# ---------------------------------------------------------------------------


class TestCrossVerifyFact:
    """C1: one extra search; if conflict, one fetch; adopt best-supported value."""

    def _import(self) -> Callable[..., str]:
        from benchmarks.gaia.precision import cross_verify_fact

        return cross_verify_fact

    # ------------------------------------------------------------------ agree
    def test_no_conflict_returns_draft_unchanged(self) -> None:
        """When search agrees with draft, return draft unchanged."""
        fn = self._import()
        result = fn(
            "How many species were recorded?",
            "6",
            search_fn=_fake_search_no_conflict,
            fetch_fn=_fake_fetch_supports_6,
            model=_model_agree,
        )
        assert result == "6"

    def test_no_conflict_fetch_never_called(self) -> None:
        """When there is no conflict, fetch_fn must NOT be called."""
        fn = self._import()
        fetch_calls: list[str] = []

        def _recording_fetch(url: str) -> str:
            fetch_calls.append(url)
            return _fake_fetch_supports_6(url)

        fn(
            "How many species?",
            "6",
            search_fn=_fake_search_no_conflict,
            fetch_fn=_recording_fetch,
            model=_model_agree,
        )
        assert fetch_calls == [], "fetch_fn must not be called when no conflict"

    # --------------------------------------------------------------- conflict
    def test_conflict_two_sources_agree_returns_corrected(self) -> None:
        """When conflict: fetch confirms 6; draft was 7; must return 6."""
        fn = self._import()
        result = fn(
            "How many box office hits?",
            "7",
            search_fn=_fake_search_conflict,
            fetch_fn=_fake_fetch_supports_6,
            model=_model_conflict_adopt_new,
        )
        assert result == "6"

    def test_conflict_fetch_is_called_once(self) -> None:
        """On conflict, fetch must be invoked exactly once."""
        fn = self._import()
        fetch_calls: list[str] = []

        def _counting_fetch(url: str) -> str:
            fetch_calls.append(url)
            return _fake_fetch_supports_6(url)

        fn(
            "How many?",
            "7",
            search_fn=_fake_search_conflict,
            fetch_fn=_counting_fetch,
            model=_model_conflict_adopt_new,
        )
        assert len(fetch_calls) == 1, "fetch_fn must be called exactly once on conflict"

    # ----------------------------------------------------------- unverifiable
    def test_unverifiable_evidence_returns_draft(self) -> None:
        """Vague / uncertain search result → draft unchanged."""
        fn = self._import()
        result = fn(
            "What is the record?",
            "draft_value",
            search_fn=_fake_search_unverifiable,
            fetch_fn=_fake_fetch_supports_6,
            model=_model_unverifiable,
        )
        assert result == "draft_value"

    # ------------------------------------------------------------ fail-soft
    def test_search_error_returns_draft_never_raises(self) -> None:
        """search_fn raising → draft returned, never propagates the exception."""
        fn = self._import()
        result = fn(
            "Some question",
            "original",
            search_fn=_raising_search,
            fetch_fn=_fake_fetch_supports_6,
            model=_model_agree,
        )
        assert result == "original"

    def test_model_error_returns_draft_never_raises(self) -> None:
        """model callable raising → draft returned, never propagates."""
        fn = self._import()

        def _raising_model(prompt: str) -> str:
            raise RuntimeError("model crashed")

        result = fn(
            "Some question",
            "original",
            search_fn=_fake_search_conflict,
            fetch_fn=_fake_fetch_supports_6,
            model=_raising_model,
        )
        assert result == "original"

    # --------------------------------------------------- search cap ≤1
    def test_search_called_at_most_once(self) -> None:
        """Bounded: search_fn must be called at most once (the single extra search)."""
        fn = self._import()
        search_calls: list[str] = []

        def _counting_search(query: str) -> str:
            search_calls.append(query)
            return _fake_search_no_conflict(query)

        fn(
            "Some question",
            "6",
            search_fn=_counting_search,
            fetch_fn=_fake_fetch_supports_6,
            model=_model_agree,
        )
        assert len(search_calls) <= 1

    def test_max_extra_searches_respected(self) -> None:
        """max_extra_searches=0 → search never called, draft unchanged."""
        fn = self._import()
        search_calls: list[str] = []

        def _counting_search(query: str) -> str:
            search_calls.append(query)
            return _fake_search_conflict(query)

        result = fn(
            "Some question",
            "7",
            search_fn=_counting_search,
            fetch_fn=_fake_fetch_supports_6,
            model=_model_conflict_adopt_new,
            max_extra_searches=0,
        )
        assert search_calls == []
        assert result == "7"

    def test_max_extra_fetches_respected(self) -> None:
        """max_extra_fetches=0 → fetch never called, draft unchanged even on conflict."""
        fn = self._import()
        fetch_calls: list[str] = []

        def _counting_fetch(url: str) -> str:
            fetch_calls.append(url)
            return _fake_fetch_supports_6(url)

        result = fn(
            "Some question",
            "7",
            search_fn=_fake_search_conflict,
            fetch_fn=_counting_fetch,
            model=_model_conflict_adopt_new,
            max_extra_fetches=0,
        )
        assert fetch_calls == []
        # When fetch cap prevents resolution, draft is preserved
        assert result == "7"


# ---------------------------------------------------------------------------
# Helpers for PR2
# ---------------------------------------------------------------------------


def _raising_exec(code: str) -> str:
    raise RuntimeError("exec error")


def _model_emit_code_matching(prompt: str) -> str:
    """Fake model: emits python that produces 7 (same as draft)."""
    return "```python\nresult = 3 + 4\n```"


def _model_emit_code_mismatch(prompt: str) -> str:
    """Fake model: emits python that produces 42 (differs from draft)."""
    return "```python\nresult = 6 * 7\n```"


def _model_emit_no_code(prompt: str) -> str:
    """Fake model: emits no code block."""
    return "I cannot derive this from the given quantities."


def _exec_eval(code: str) -> str:
    """Minimal exec: eval the last assignment and return its str repr."""
    for line in code.strip().splitlines():
        if line.strip().startswith("result"):
            try:
                rhs = line.split("=", 1)[1].strip()
                val = eval(rhs)  # noqa: S307 — hermetic test only
                return str(val)
            except Exception:
                return "ERROR"
    return "ERROR"


# ---------------------------------------------------------------------------
# PR2: recompute_numeric
# ---------------------------------------------------------------------------


class TestRecomputeNumeric:
    """C2: re-derive numeric answer via exec; adopt if disagrees with draft."""

    def _import(self) -> Callable[..., str]:
        from benchmarks.gaia.precision import recompute_numeric

        return recompute_numeric

    # ----------------------------------------------------------- matching
    def test_matching_result_returns_draft_unchanged(self) -> None:
        """Code produces 7 (same as draft) → draft unchanged."""
        fn = self._import()
        result = fn(
            "What is 3 + 4?",
            "7",
            "question states 3 + 4",
            exec_fn=_exec_eval,
            model=_model_emit_code_matching,
        )
        assert result == "7"

    # ---------------------------------------------------------- mismatch
    def test_mismatch_returns_code_result(self) -> None:
        """Code produces 42 (differs from draft=7) → returns '42'."""
        fn = self._import()
        result = fn(
            "What is 6 * 7?",
            "7",
            "question states 6 * 7",
            exec_fn=_exec_eval,
            model=_model_emit_code_mismatch,
        )
        assert result == "42"

    # ------------------------------------------------------- non-numeric draft
    def test_non_numeric_draft_returns_unchanged(self) -> None:
        """Non-numeric draft (e.g. a name) → unchanged, no exec attempted."""
        fn = self._import()
        result = fn(
            "Who was president?",
            "Abraham Lincoln",
            "some evidence",
            exec_fn=_exec_eval,
            model=_model_emit_code_matching,
        )
        assert result == "Abraham Lincoln"

    # -------------------------------------------------------------- exec error
    def test_exec_error_returns_draft(self) -> None:
        """exec_fn raising → draft returned, never propagates."""
        fn = self._import()
        result = fn(
            "What is the volume?",
            "55",
            "volume calculation evidence",
            exec_fn=_raising_exec,
            model=_model_emit_code_mismatch,
        )
        assert result == "55"

    # ------------------------------------------------------ model returns no code
    def test_no_code_returned_by_model_returns_draft(self) -> None:
        """Model emits no code block → draft unchanged."""
        fn = self._import()
        result = fn(
            "What is the count?",
            "55",
            "evidence here",
            exec_fn=_exec_eval,
            model=_model_emit_no_code,
        )
        assert result == "55"

    # ----------------------------------------------------------- fail-soft
    def test_model_error_returns_draft(self) -> None:
        """model callable raising → draft returned, never propagates."""
        fn = self._import()

        def _raising_model(prompt: str) -> str:
            raise RuntimeError("model crashed")

        result = fn(
            "What is the volume?",
            "55",
            "evidence",
            exec_fn=_exec_eval,
            model=_raising_model,
        )
        assert result == "55"


# ---------------------------------------------------------------------------
# PR3: apply_precision_pass
# ---------------------------------------------------------------------------


class TestApplyPrecisionPass:
    """Dispatch + gate: mode=off|audit|enforce, default-OFF."""

    def _import(self) -> Callable[..., str]:
        from benchmarks.gaia.precision import apply_precision_pass

        return apply_precision_pass

    # ----------------------------------------------------------------- off
    def test_mode_off_passthrough_no_fns_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mode=off → returns draft unchanged without calling any fn."""
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "off")
        fn = self._import()

        search_calls: list[str] = []
        exec_calls: list[str] = []

        def _search(q: str) -> str:
            search_calls.append(q)
            return _fake_search_conflict(q)

        def _exec(code: str) -> str:
            exec_calls.append(code)
            return _exec_eval(code)

        result = fn(
            question="How many box office records?",
            draft="7",
            evidence="Some evidence text.",
            mode="off",
            search_fn=_search,
            fetch_fn=_fake_fetch_supports_6,
            exec_fn=_exec,
            model=_model_conflict_adopt_new,
        )
        assert result == "7"
        assert search_calls == []
        assert exec_calls == []

    def test_default_mode_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When MAGI_GAIA_PRECISION is not set, default is off (passthrough)."""
        monkeypatch.delenv("MAGI_GAIA_PRECISION", raising=False)
        fn = self._import()
        result = fn(
            question="How many?",
            draft="7",
            evidence="Some evidence.",
            mode=None,  # should resolve to off
            search_fn=_fake_search_conflict,
            fetch_fn=_fake_fetch_supports_6,
            exec_fn=_exec_eval,
            model=_model_conflict_adopt_new,
        )
        assert result == "7"

    # ---------------------------------------------------------------- audit
    def test_audit_mode_does_not_change_answer_even_when_conflict_found(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """audit mode: computes correction internally but returns draft unchanged."""
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "audit")
        fn = self._import()

        with caplog.at_level(logging.DEBUG, logger="magi_agent"):
            result = fn(
                question="How many box office hits?",
                draft="7",
                evidence="Sources confirm 6 hits total.",
                mode="audit",
                search_fn=_fake_search_conflict,
                fetch_fn=_fake_fetch_supports_6,
                exec_fn=_exec_eval,
                model=_model_conflict_adopt_new,
            )

        # draft unchanged in audit mode
        assert result == "7"

    # --------------------------------------------------------------- enforce
    def test_enforce_mode_applies_c1_for_web_fact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """enforce + short web-fact draft → C1 runs, correction applied."""
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "enforce")
        fn = self._import()
        result = fn(
            question="How many box office hits did the film achieve?",
            draft="7",
            evidence="Box office records from official source.",
            mode="enforce",
            search_fn=_fake_search_conflict,
            fetch_fn=_fake_fetch_supports_6,
            exec_fn=_exec_eval,
            model=_model_conflict_adopt_new,
        )
        assert result == "6"

    def test_enforce_mode_applies_c2_for_numeric_calculation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """enforce + 'average volume' numeric question → C2 runs, correction applied."""
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "enforce")
        fn = self._import()
        result = fn(
            question="What is the average volume in cubic meters?",
            draft="7",
            evidence="The values are 6, 7, and 8 (average = 7).",
            mode="enforce",
            search_fn=_fake_search_no_conflict,
            fetch_fn=_fake_fetch_supports_6,
            exec_fn=_exec_eval,
            model=_model_emit_code_mismatch,  # code emits 42
        )
        # C2 fires: code says 42, draft is 7 → adopt 42
        assert result == "42"

    def test_enforce_mode_no_trigger_for_long_non_numeric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A long answer string (>50 chars) is not a short web-fact → no C1 trigger."""
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "enforce")
        fn = self._import()
        long_draft = "The quick brown fox jumps over the lazy dog and then does something else"
        result = fn(
            question="Describe the event in detail.",
            draft=long_draft,
            evidence="",
            mode="enforce",
            search_fn=_fake_search_conflict,
            fetch_fn=_fake_fetch_supports_6,
            exec_fn=_exec_eval,
            model=_model_conflict_adopt_new,
        )
        # Too long to be a short fact → no trigger → pass through unchanged
        assert result == long_draft

    # ------------------------------------------------ over-correction guard
    def test_no_free_reguess_without_evidence_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Core guard: when search AGREES with draft, draft must be returned unchanged.

        This is the anti-over-correction test: the answer verifier was removed
        because it changed e.g. 'backtick' → 'grave'. The precision pass must
        ONLY correct on a genuine conflict signal grounded in evidence.
        """
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "enforce")
        fn = self._import()
        result = fn(
            question="What is the symbol for backtick?",
            draft="backtick",
            evidence="Standard name: backtick.",
            mode="enforce",
            search_fn=_fake_search_no_conflict,  # agrees with draft
            fetch_fn=_fake_fetch_supports_6,
            exec_fn=_exec_eval,
            model=_model_agree,  # model says AGREE
        )
        # Must NOT change 'backtick' → something else when no conflict
        assert result == "backtick"

    # -------------------------------------------------------- fail-soft e2e
    def test_all_fns_raising_returns_draft(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even when all injected fns raise, precision pass must return draft."""
        monkeypatch.setenv("MAGI_GAIA_PRECISION", "enforce")
        fn = self._import()

        def _boom(arg: str) -> str:
            raise RuntimeError("boom")

        result = fn(
            question="How many?",
            draft="original",
            evidence="",
            mode="enforce",
            search_fn=_boom,
            fetch_fn=_boom,
            exec_fn=_boom,
            model=_boom,
        )
        assert result == "original"
