"""Conservative orchestration defaults — Principle 3 (P3).

Advanced orchestration capabilities (ledger orchestrator, deep-web research,
answer verifier) MUST be default-OFF.  This test file locks those defaults so
they cannot accidentally be turned on by a configuration change.

Policy: advanced orchestration is default-OFF and enabled only with measured
evidence for the task class.  See ``magi_agent/recipes/README.md`` for details.
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# MAGI_LEDGER_ORCHESTRATOR_ENABLED — default OFF
# ---------------------------------------------------------------------------


def test_ledger_orchestrator_disabled_by_default(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """``run_with_ledger_orchestrator`` returns None when env var is absent (default-OFF)."""
    monkeypatch.delenv("MAGI_LEDGER_ORCHESTRATOR_ENABLED", raising=False)

    from magi_agent.recipes.ledger_orchestrator import _ledger_orchestrator_enabled

    assert _ledger_orchestrator_enabled() is False, (
        "MAGI_LEDGER_ORCHESTRATOR_ENABLED must default to OFF (False) "
        "when the env var is absent."
    )


def test_ledger_orchestrator_run_returns_none_when_disabled(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """``run_with_ledger_orchestrator`` short-circuits to None when flag is not set."""
    monkeypatch.delenv("MAGI_LEDGER_ORCHESTRATOR_ENABLED", raising=False)

    from magi_agent.recipes.ledger_orchestrator import run_with_ledger_orchestrator

    def _noop_executor(step: object, task_ledger: object, progress_ledger: object) -> object:
        raise AssertionError("step_executor must not be called when orchestrator is disabled")

    result = run_with_ledger_orchestrator(
        ledger_id="test-ledger",
        objective_text="Does the disabled gate fire?",
        step_executor=_noop_executor,  # type: ignore[arg-type]
    )

    assert result is None, (
        "run_with_ledger_orchestrator must return None (not execute) "
        "when MAGI_LEDGER_ORCHESTRATOR_ENABLED is absent."
    )


def test_ledger_orchestrator_config_default_off_literal() -> None:
    """``LedgerOrchestratorConfig.default_off`` is the Literal[True] authority flag."""
    from magi_agent.recipes.ledger_orchestrator import LedgerOrchestratorConfig
    from magi_agent.recipes.ledger_budget import default_gaia_policy

    config = LedgerOrchestratorConfig(budget_policy=default_gaia_policy(2))
    assert config.default_off is True, (
        "LedgerOrchestratorConfig.default_off must be Literal[True] — "
        "it is the authority flag signalling this capability is default-OFF."
    )


# ---------------------------------------------------------------------------
# DeepResearchConfig — default OFF
# ---------------------------------------------------------------------------


def test_deep_research_config_default_enabled_false() -> None:
    """``DeepResearchConfig()`` must have ``enabled=False`` by default."""
    from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig

    config = DeepResearchConfig()
    assert config.enabled is False, (
        "DeepResearchConfig.enabled must default to False — "
        "deep-web research is default-OFF (Principle 3)."
    )


def test_deep_research_config_from_env_default_off(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """``deep_research_config_from_env()`` returns enabled=False when env var absent."""
    import pytest

    monkeypatch.delenv("MAGI_DEEP_WEB_RESEARCH_ENABLED", raising=False)

    from magi_agent.web_acquisition.deep_research_config import deep_research_config_from_env

    config = deep_research_config_from_env()
    assert config.enabled is False, (
        "deep_research_config_from_env must return enabled=False when "
        "MAGI_DEEP_WEB_RESEARCH_ENABLED is not set."
    )


def test_deep_research_orchestrator_returns_disabled_when_config_off() -> None:
    """``DeepWebResearchOrchestrator.research`` returns disabled result when config off."""
    import asyncio

    from magi_agent.web_acquisition.deep_research import (
        DeepWebResearchOrchestrator,
    )
    from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig

    class _FakeBoundary:
        _env: object = None

    config = DeepResearchConfig(enabled=False)
    orchestrator = DeepWebResearchOrchestrator(
        boundary=_FakeBoundary(),  # type: ignore[arg-type]
        config=config,
    )

    result = asyncio.run(orchestrator.research("What year did GAIA launch?"))

    assert result.status == "disabled", (
        "DeepWebResearchOrchestrator must return status='disabled' immediately "
        "when DeepResearchConfig.enabled is False."
    )
    assert result.queries_issued == 0


# ---------------------------------------------------------------------------
# AnswerVerifier — default OFF
# ---------------------------------------------------------------------------


def test_answer_verifier_mode_default_off(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """MAGI_ANSWER_VERIFIER_MODE defaults to 'off' when env var is absent."""
    import pytest

    monkeypatch.delenv("MAGI_ANSWER_VERIFIER_MODE", raising=False)

    # The mode reader lives in the GAIA plugin (the only env-reading site).
    from magi_agent.benchmarks.gaia.answer_verifier_plugin import _get_mode

    mode = _get_mode()
    assert mode == "off", (
        "MAGI_ANSWER_VERIFIER_MODE must default to 'off' — "
        "answer verification is default-OFF (Principle 3)."
    )


def test_answer_verifier_off_mode_is_noop() -> None:
    """``evaluate_answer_verifier`` with mode='off' returns skipped result unchanged."""
    from magi_agent.research.answer_verifier import (
        AnswerVerifierEvidencePayload,
        AnswerVerifierRequest,
        evaluate_answer_verifier,
    )

    request = AnswerVerifierRequest(
        verifier_id="test-verifier",
        mode="off",
        question="What is 2+2?",
        final_answer="4",
        evidence_payload=AnswerVerifierEvidencePayload(
            question="What is 2+2?",
            final_answer="4",
            evidence_snippets=("Two plus two equals four.",),
        ),
        model_provider=None,
    )

    result = evaluate_answer_verifier(request)

    assert result.status == "skipped"
    assert result.correction_applied is False
    assert result.verified_answer == "4"
    assert result.execution_posture.default_off is True


# ---------------------------------------------------------------------------
# All three flags together — a single snapshot assertion
# ---------------------------------------------------------------------------


def test_all_advanced_orchestration_defaults_are_off(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Snapshot: ledger, deep-research, and verifier are ALL default-OFF simultaneously."""
    import pytest

    monkeypatch.delenv("MAGI_LEDGER_ORCHESTRATOR_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_DEEP_WEB_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_ANSWER_VERIFIER_MODE", raising=False)

    from magi_agent.recipes.ledger_orchestrator import _ledger_orchestrator_enabled
    from magi_agent.web_acquisition.deep_research_config import (
        DeepResearchConfig,
        deep_research_config_from_env,
    )
    from magi_agent.benchmarks.gaia.answer_verifier_plugin import _get_mode

    assert _ledger_orchestrator_enabled() is False, "ledger orchestrator must default OFF"
    assert deep_research_config_from_env().enabled is False, "deep-web research must default OFF"
    assert DeepResearchConfig().enabled is False, "DeepResearchConfig.enabled must default False"
    assert _get_mode() == "off", "answer verifier must default to 'off'"
