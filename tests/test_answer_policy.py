"""Tests for P6 answer-policy seam (MAGI_ANSWER_POLICY).

TDD: tests written BEFORE implementation.
RED → GREEN cycle.

Principle 6 (GAIA learnings): answer policy (commit vs abstain) is task-dependent
and configurable. Production default = abstain (honest). Benchmark layer sets
commit. The *seam* lives first-party; the forced-answer behavior stays in
benchmarks/gaia/.

Tested behaviour
----------------
* ``answer_policy()`` returns ``"abstain"`` by default (unset env).
* ``MAGI_ANSWER_POLICY=commit`` → returns ``"commit"``.
* ``MAGI_ANSWER_POLICY=abstain`` → returns ``"abstain"`` explicitly.
* Unknown/invalid values fall back to ``"abstain"`` (production-safe default).
* ``should_force_answer()`` returns False when policy=abstain, True when commit.
* Both functions accept an optional explicit env mapping.
* Anti-overfitting: answer_policy module must not import from benchmarks.gaia.
"""
from __future__ import annotations

import importlib

import pytest

from magi_agent.research.answer_policy import (
    ANSWER_POLICY_ENV,
    AnswerPolicy,
    answer_policy,
    should_force_answer,
)


# ---------------------------------------------------------------------------
# Anti-overfitting firewall
# ---------------------------------------------------------------------------


def test_answer_policy_does_not_import_gaia() -> None:
    """answer_policy.py must not import from benchmarks.gaia — structural firewall."""
    mod = importlib.import_module("magi_agent.research.answer_policy")
    if mod.__file__:
        with open(mod.__file__) as fh:
            source = fh.read()
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from ")) and "benchmarks.gaia" in line
        ]
        assert import_lines == [], (
            f"answer_policy.py must not import from benchmarks.gaia: {import_lines}"
        )


# ---------------------------------------------------------------------------
# ANSWER_POLICY_ENV constant
# ---------------------------------------------------------------------------


def test_answer_policy_env_constant() -> None:
    assert ANSWER_POLICY_ENV == "MAGI_ANSWER_POLICY"


# ---------------------------------------------------------------------------
# answer_policy() — env-var resolution
# ---------------------------------------------------------------------------


class TestAnswerPolicyFunction:
    def test_default_is_abstain_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_ANSWER_POLICY", raising=False)
        assert answer_policy() == "abstain"

    def test_explicit_abstain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "abstain")
        assert answer_policy() == "abstain"

    def test_explicit_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "commit")
        assert answer_policy() == "commit"

    def test_case_insensitive_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "COMMIT")
        assert answer_policy() == "commit"

    def test_case_insensitive_abstain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "ABSTAIN")
        assert answer_policy() == "abstain"

    def test_unknown_value_falls_back_to_abstain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "unknown_policy")
        assert answer_policy() == "abstain"

    def test_empty_string_falls_back_to_abstain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "")
        assert answer_policy() == "abstain"

    def test_accepts_explicit_env_mapping_commit(self) -> None:
        assert answer_policy(env={"MAGI_ANSWER_POLICY": "commit"}) == "commit"

    def test_accepts_explicit_env_mapping_abstain(self) -> None:
        assert answer_policy(env={"MAGI_ANSWER_POLICY": "abstain"}) == "abstain"

    def test_accepts_empty_env_mapping_defaults_abstain(self) -> None:
        assert answer_policy(env={}) == "abstain"

    def test_return_type_is_literal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returned value must be one of the AnswerPolicy literal values."""
        for val in ("abstain", "commit"):
            monkeypatch.setenv("MAGI_ANSWER_POLICY", val)
            result = answer_policy()
            assert result in ("abstain", "commit"), f"unexpected value {result!r}"


# ---------------------------------------------------------------------------
# should_force_answer()
# ---------------------------------------------------------------------------


class TestShouldForceAnswer:
    def test_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Production default: do not force answer (allow abstention)."""
        monkeypatch.delenv("MAGI_ANSWER_POLICY", raising=False)
        assert should_force_answer() is False

    def test_abstain_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "abstain")
        assert should_force_answer() is False

    def test_commit_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "commit")
        assert should_force_answer() is True

    def test_accepts_explicit_env_mapping(self) -> None:
        assert should_force_answer(env={"MAGI_ANSWER_POLICY": "commit"}) is True
        assert should_force_answer(env={"MAGI_ANSWER_POLICY": "abstain"}) is False
        assert should_force_answer(env={}) is False

    def test_unknown_policy_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_ANSWER_POLICY", "force_it")
        assert should_force_answer() is False

    def test_return_type_is_bool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val, expected in (("commit", True), ("abstain", False)):
            monkeypatch.setenv("MAGI_ANSWER_POLICY", val)
            result = should_force_answer()
            assert isinstance(result, bool)
            assert result is expected


# ---------------------------------------------------------------------------
# AnswerPolicy type alias
# ---------------------------------------------------------------------------


def test_answer_policy_type_alias_values() -> None:
    """AnswerPolicy is a Literal type covering exactly 'abstain' and 'commit'."""
    import typing

    args = typing.get_args(AnswerPolicy)
    assert set(args) == {"abstain", "commit"}, f"unexpected args: {args}"
