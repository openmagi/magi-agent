"""Tests for the first-party best-effort finalization mechanism (TDD — written first).

HAL learnings (2026-06-11): HAL's generalist agent never returns an empty
answer — when budget runs out, a forced-synthesis prompt makes the model commit
to a best guess from accumulated context. ``magi_agent.runtime.best_effort_answer``
generalizes the previously benchmark-private ``benchmarks/gaia/forced_answer.py``
mechanism into a first-party, policy-gated module.

Contract under test
-------------------
* Default-OFF proof: with ``MAGI_ANSWER_POLICY`` unset (or ``env={}``),
  ``finalize_answer`` is a byte-identical pass-through and never calls the
  model provider.
* ``commit`` + real answer → unchanged, provider not called.
* ``commit`` + non-answer → exactly one synthesis call; result optionally
  carries the uncertainty label.
* Fail-open on provider exception / non-str reply / abstaining reply.
* Evidence is head+tail capped; reply is length capped.
* Anti-overfitting firewall: the module must not import from ``benchmarks.``.
"""
from __future__ import annotations

import importlib

import pytest

from magi_agent.runtime.best_effort_answer import (
    DEFAULT_UNCERTAINTY_LABEL,
    BestEffortConfig,
    FinalAnswer,
    finalize_answer,
    is_non_answer,
)


class _CountingProvider:
    """Fake model provider that records prompts and returns a fixed reply."""

    def __init__(self, reply: str = "Paris") -> None:
        self.prompts: list[str] = []
        self._reply = reply

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._reply


# ---------------------------------------------------------------------------
# 1. Default-OFF = zero behavior change (the proof test)
# ---------------------------------------------------------------------------


class TestDefaultOffIsPassThrough:
    @pytest.mark.parametrize("candidate", ["", "I cannot determine the answer."])
    def test_env_unset_returns_candidate_byte_identical(
        self, monkeypatch: pytest.MonkeyPatch, candidate: str
    ) -> None:
        monkeypatch.delenv("MAGI_ANSWER_POLICY", raising=False)
        provider = _CountingProvider()
        result = finalize_answer("Q?", candidate, "some evidence", provider)
        assert result == FinalAnswer(text=candidate, synthesized=False, policy="abstain")
        assert result.text is candidate, "abstain must not mutate the candidate"
        assert provider.prompts == [], "abstain must never call the provider"

    @pytest.mark.parametrize("candidate", ["", "I cannot determine the answer."])
    def test_explicit_empty_env_returns_candidate_byte_identical(
        self, candidate: str
    ) -> None:
        provider = _CountingProvider()
        result = finalize_answer("Q?", candidate, "some evidence", provider, env={})
        assert result == FinalAnswer(text=candidate, synthesized=False, policy="abstain")
        assert result.text is candidate
        assert provider.prompts == []


# ---------------------------------------------------------------------------
# 2. Commit + real answer → unchanged
# ---------------------------------------------------------------------------


def test_commit_with_real_answer_is_unchanged_and_provider_not_called() -> None:
    provider = _CountingProvider()
    candidate = "42"
    result = finalize_answer(
        "Q?", candidate, "evidence", provider, env={"MAGI_ANSWER_POLICY": "commit"}
    )
    assert result == FinalAnswer(text=candidate, synthesized=False, policy="commit")
    assert result.text is candidate, "a real answer is never overwritten"
    assert provider.prompts == []


# ---------------------------------------------------------------------------
# 3. Commit + non-answer → synthesis
# ---------------------------------------------------------------------------


class TestCommitSynthesis:
    def test_provider_called_once_with_question_and_evidence(self) -> None:
        provider = _CountingProvider(reply="Paris")
        result = finalize_answer(
            "What is the capital of France?",
            "",
            "Gathered evidence XYZ.",
            provider,
            env={"MAGI_ANSWER_POLICY": "commit"},
        )
        assert len(provider.prompts) == 1
        assert "What is the capital of France?" in provider.prompts[0]
        assert "Gathered evidence XYZ." in provider.prompts[0]
        assert result.synthesized is True
        assert result.policy == "commit"

    def test_labeled_when_label_uncertainty_true(self) -> None:
        provider = _CountingProvider(reply="Paris")
        result = finalize_answer(
            "Q?",
            "",
            "evidence",
            provider,
            env={"MAGI_ANSWER_POLICY": "commit"},
            config=BestEffortConfig(label_uncertainty=True),
        )
        assert result.text == "Paris\n" + DEFAULT_UNCERTAINTY_LABEL

    def test_unlabeled_when_label_uncertainty_false(self) -> None:
        provider = _CountingProvider(reply="Paris")
        result = finalize_answer(
            "Q?",
            "",
            "evidence",
            provider,
            env={"MAGI_ANSWER_POLICY": "commit"},
            config=BestEffortConfig(label_uncertainty=False),
        )
        assert result.text == "Paris"

    def test_empty_evidence_uses_placeholder(self) -> None:
        provider = _CountingProvider(reply="Paris")
        finalize_answer(
            "Q?", "", "   \n", provider, env={"MAGI_ANSWER_POLICY": "commit"}
        )
        assert "(no additional evidence gathered)" in provider.prompts[0]

    def test_reply_truncated_to_max_answer_chars(self) -> None:
        provider = _CountingProvider(reply="x" * 5_000)
        result = finalize_answer(
            "Q?",
            "",
            "evidence",
            provider,
            env={"MAGI_ANSWER_POLICY": "commit"},
            config=BestEffortConfig(label_uncertainty=False, max_answer_chars=2_000),
        )
        assert result.text == "x" * 2_000


# ---------------------------------------------------------------------------
# 4. Fail-open paths
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_provider_exception_returns_candidate(self) -> None:
        def raising_provider(prompt: str) -> str:
            raise RuntimeError("network down")

        candidate = "cannot determine"
        result = finalize_answer(
            "Q?", candidate, "evidence", raising_provider,
            env={"MAGI_ANSWER_POLICY": "commit"},
        )
        assert result == FinalAnswer(text=candidate, synthesized=False, policy="commit")
        assert result.text is candidate

    def test_provider_returning_non_str_returns_candidate(self) -> None:
        def bad_provider(prompt: str) -> str:
            return 42  # type: ignore[return-value]

        result = finalize_answer(
            "Q?", "", "evidence", bad_provider, env={"MAGI_ANSWER_POLICY": "commit"}
        )
        assert result == FinalAnswer(text="", synthesized=False, policy="commit")

    # ------------------------------------------------------------------
    # 5. Synthesized reply itself abstains → keep candidate
    # ------------------------------------------------------------------

    def test_abstaining_reply_returns_candidate(self) -> None:
        provider = _CountingProvider(reply="I am unable to determine the answer.")
        candidate = ""
        result = finalize_answer(
            "Q?", candidate, "evidence", provider, env={"MAGI_ANSWER_POLICY": "commit"}
        )
        assert result == FinalAnswer(text=candidate, synthesized=False, policy="commit")
        assert len(provider.prompts) == 1


# ---------------------------------------------------------------------------
# 6. Evidence head+tail cap
# ---------------------------------------------------------------------------


def test_long_evidence_is_head_tail_capped() -> None:
    provider = _CountingProvider(reply="Paris")
    cap = 24_000
    evidence = "HEAD_SENTINEL " + ("x" * 60_000) + " TAIL_SENTINEL"
    finalize_answer(
        "Q?",
        "",
        evidence,
        provider,
        env={"MAGI_ANSWER_POLICY": "commit"},
        config=BestEffortConfig(max_evidence_chars=cap),
    )
    prompt = provider.prompts[0]
    assert "HEAD_SENTINEL" in prompt, "head of evidence must survive the cap"
    assert "TAIL_SENTINEL" in prompt, "tail of evidence must survive the cap"
    assert "…[truncated]…" in prompt
    # Prompt = template + question + capped evidence; bound it well under raw size.
    assert len(prompt) < cap + 1_000
    assert len(evidence) > cap + 1_000  # the cap actually did something


# ---------------------------------------------------------------------------
# 7. is_non_answer table test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", True),
        ("   \n\t  ", True),
        ("I am unable to determine the answer.", True),
        ("I cannot determine the final answer.", True),
        ("I can not determine this.", True),
        ("There is insufficient information to answer.", True),
        ("Awaiting approval from the user.", True),
        ("I don't know", True),
        ("I do not know the answer.", True),
        ("42", False),
        ("Paris", False),
        ("the quick brown fox", False),
        ("knot", False),
    ],
)
def test_is_non_answer_table(text: str, expected: bool) -> None:
    assert is_non_answer(text) is expected


# ---------------------------------------------------------------------------
# 8. Anti-overfitting firewall
# ---------------------------------------------------------------------------


def test_best_effort_answer_does_not_import_benchmarks() -> None:
    """best_effort_answer.py must not import from benchmarks — structural firewall."""
    mod = importlib.import_module("magi_agent.runtime.best_effort_answer")
    assert mod.__file__ is not None
    with open(mod.__file__) as fh:
        source = fh.read()
    import_lines = [
        line for line in source.splitlines()
        if line.strip().startswith(("import ", "from ")) and "benchmarks" in line
    ]
    assert import_lines == [], (
        f"best_effort_answer.py must not import from benchmarks: {import_lines}"
    )
