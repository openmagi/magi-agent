"""Per-test isolation fixtures for the F-QA matrix harness.

Provides:

* :func:`session_id` — unique per test for ledger isolation.
* :func:`customize_path_fixture` — tmp ``customize.json`` + the
  ``MAGI_CUSTOMIZE`` env override that re-routes the runtime store to it.
* :func:`flags_on` — flips every relevant master flag ON for the test
  session. Verification / custom_rules / per-kind mutator + lifecycle +
  EXEC1/2 + egress (for the llm_criterion critic). The fixture restores
  the original env via ``monkeypatch`` so concurrent tests cannot leak
  state across rows.
* :func:`patched_judge` — monkeypatches the LLM criterion engine so
  llm_criterion tests do not require a real provider key. The pre-final
  / after-tool paths invoke the same ``evaluate_criterion`` entry point
  used in production; the fake records calls + returns a verdict the
  test selects per row (``passed`` ⇒ no block; ``failed`` ⇒ block +
  reason).
* :func:`provider_key_skip` — pytest-skip helper for tests that opt into
  a real LLM round-trip (none in F-QA1; F-QA3 will use it).
* :func:`shell_budget_reset` — resets the per-turn shell budget map
  between tests so concurrent shell_command / shell_check rows do not
  share counters.
* :func:`active_turn_identity` — installs / tears down the active
  (session, turn) identity the shell fan-out helpers read via
  ``shell_budget_for``.

Each fixture is intentionally narrow so test files can opt in to only
the isolation they need.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Identity / storage isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def session_id() -> str:
    """Unique session id per test — keeps the shell-budget map per-row."""
    return f"sess_fqa1_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def turn_id() -> str:
    """Unique turn id per test (pairs with session_id for the shell budget)."""
    return f"turn_fqa1_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def customize_path_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Tmp ``customize.json`` + ``MAGI_CUSTOMIZE`` env override.

    The store's :func:`magi_agent.customize.store.customize_path` reads
    the env override before falling back to ``~/.magi/customize.json``,
    so this fixture is the seam every test uses to author rules without
    touching the operator's real config.
    """
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


# ---------------------------------------------------------------------------
# Master-flag activation
# ---------------------------------------------------------------------------


_FQA1_MASTER_FLAGS: tuple[str, ...] = (
    # Verification + custom_rules (every kind needs these two ON).
    "MAGI_CUSTOMIZE_VERIFICATION_ENABLED",
    "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED",
    # llm_criterion critic model factory (the pre-final + after-tool
    # gates short-circuit when MAGI_EGRESS_GATE_ENABLED is OFF).
    "MAGI_EGRESS_GATE_ENABLED",
    # F-MUT1 / F-MUT2 mutator master flags.
    "MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED",
    "MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED",
    # F-LIFE1 / F-LIFE2 / F-LIFE3 / F-LIFE4a / F-LIFE4b lifecycle
    # expansion — slot fan-out helpers consult these.
    "MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED",
    "MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED",
    "MAGI_CUSTOMIZE_LIFECYCLE_LLM_CALL_HOOKS_ENABLED",
    "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED",
    "MAGI_CUSTOMIZE_SESSION_TASK_EMITTERS_ENABLED",
    # F-EXEC1 / F-EXEC2 — operator shell rules.
    "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED",
    "MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED",
    # SHACL kernel for shacl_constraint rules.
    "MAGI_SHACL_VERIFIER_ENABLED",
)


@pytest.fixture
def flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip every F-QA1 master flag ON for the duration of the test.

    ``monkeypatch`` undoes the change after the test, so flag state
    never bleeds across rows even when pytest runs the matrix
    sequentially in a single process.
    """
    for flag in _FQA1_MASTER_FLAGS:
        monkeypatch.setenv(flag, "1")
    # Profile keys should stay unset — lab profile would seed extra
    # behaviors that mask which flag actually drove the firing.
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)


# ---------------------------------------------------------------------------
# Critic stub for llm_criterion
# ---------------------------------------------------------------------------


_JudgeVerdict = tuple[bool, str]


class JudgePatcher:
    """Helper that installs a fake judge + records calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._verdict: _JudgeVerdict = (True, "ok")

    def set_verdict(self, *, passed: bool, reason: str = "matrix-row") -> None:
        self._verdict = (passed, reason)

    async def __call__(
        self,
        *,
        criterion: str,
        draft_text: str,
        model_factory: object,
        invoke: object | None = None,
    ) -> _JudgeVerdict:
        self.calls.append(
            {
                "criterion": criterion,
                "draft_text": draft_text,
                "model_factory": model_factory,
                "invoke": invoke,
            }
        )
        return self._verdict


@pytest.fixture
def patched_judge(monkeypatch: pytest.MonkeyPatch) -> JudgePatcher:
    """Install a fake judge across the llm_criterion entry points.

    The same callable is patched in both the pre-final gate's import
    site (``magi_agent.customize.criterion_engine.evaluate_criterion``)
    and the after-tool gate's import site
    (``magi_agent.customize.after_tool_gate.evaluate_criterion``) so
    every llm_criterion code path resolves to the fake without a real
    provider round-trip.
    """
    patcher = JudgePatcher()
    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion",
        patcher,
    )
    monkeypatch.setattr(
        "magi_agent.customize.after_tool_gate.evaluate_criterion",
        patcher,
    )
    return patcher


# ---------------------------------------------------------------------------
# Provider key skip helper
# ---------------------------------------------------------------------------


_PROVIDER_KEY_ENVS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "FIREWORKS_API_KEY",
    "OPENROUTER_API_KEY",
)


def has_any_provider_key() -> bool:
    """Return True iff at least one supported provider key is in the env."""
    return any(os.environ.get(name) for name in _PROVIDER_KEY_ENVS)


@pytest.fixture
def require_provider_key() -> Callable[[], None]:
    """Skip the test when no real provider key is available.

    Tests that opt into a live LLM call (none in F-QA1; F-QA3 will use
    this) call ``require_provider_key()`` at the top of the test body
    so collection still happens, but the test is skipped with a clear
    reason when the operator did not set a key.
    """

    def _check() -> None:
        if not has_any_provider_key():
            pytest.skip(
                "no LLM provider key in env "
                f"(set one of {_PROVIDER_KEY_ENVS}) — required for live "
                "llm_criterion verification"
            )

    return _check


# ---------------------------------------------------------------------------
# Shell budget isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def shell_budget_reset() -> None:
    """Reset the shared shell-budget map before AND after the test."""
    from magi_agent.adk_bridge.lifecycle_shell_command_control import (
        reset_shared_budget_for_tests,
    )

    reset_shared_budget_for_tests()
    yield
    reset_shared_budget_for_tests()


@pytest.fixture
def active_turn_identity(
    session_id: str, turn_id: str, shell_budget_reset: None
):
    """Install + tear down ``set_active_turn_identity`` for shell helpers.

    Required by every test that drives a shell_command / shell_check
    fan-out helper directly (the helpers call ``shell_budget_for()``
    which reads the active identity).
    """
    from magi_agent.adk_bridge.lifecycle_shell_command_control import (
        reset_active_turn_identity,
        set_active_turn_identity,
    )

    token = set_active_turn_identity(session_id, turn_id)
    try:
        yield (session_id, turn_id)
    finally:
        reset_active_turn_identity(token)


# ---------------------------------------------------------------------------
# Cleanup hook
# ---------------------------------------------------------------------------


@pytest.fixture
def cleanup_rule(
    customize_path_fixture: Path,
) -> Callable[[str], None]:
    """Return a closure that deletes a rule by id from the per-test customize.json.

    The matrix's 5-step pattern ends with ``cleanup(rule_id)`` so the
    next row starts from an empty rule list even if pytest reuses the
    test directory across runs (it shouldn't — ``tmp_path`` is unique
    per test — but the explicit delete documents the contract).
    """
    from magi_agent.customize.store import delete_custom_rule

    def _delete(rule_id: str) -> None:
        delete_custom_rule(rule_id, path=customize_path_fixture)

    return _delete
