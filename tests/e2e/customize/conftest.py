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
* :func:`patched_critic_factory` — monkeypatches the critic-model
  factory builders so the lifecycle_audit fan-out has a non-None
  ``model_factory`` to thread into ``_audit_one_rule`` even on a
  key-less / fresh-install machine. Without this fixture every
  ``llm_criterion`` block-action row short-circuits to
  ``status="skipped"`` (``_audit_one_rule`` guards on
  ``model_factory is None``), the gate verdict collapses to
  ``"proceed"``, and the matrix asserter trips. Auto-included by
  :func:`patched_judge` so every llm_criterion test row works without
  opting in explicitly.
* :func:`patched_judge` — monkeypatches the LLM criterion engine so
  llm_criterion tests do not require a real provider key. The pre-final
  / after-tool paths invoke the same ``evaluate_criterion`` entry point
  used in production; the fake records calls + returns a verdict the
  test selects per row (``passed`` ⇒ no block; ``failed`` ⇒ block +
  reason). The fixture also pulls in :func:`patched_critic_factory`
  so the gate fan-out actually reaches ``evaluate_criterion`` on a
  key-less host (otherwise it short-circuits before the patched judge
  ever runs).
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
    # F-QA3 LLM-call slot master flag. NOTE: the historical
    # ``MAGI_CUSTOMIZE_LIFECYCLE_LLM_CALL_HOOKS_ENABLED`` name was a
    # typo — the actual env var consulted by
    # :func:`magi_agent.customize.lifecycle_audit.llm_call_hooks_enabled`
    # is ``MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED`` (no LIFECYCLE_
    # prefix). The typo'd entry is kept for byte-identical OFF-path
    # parity with the prior conftest until a separate cleanup PR
    # retires it.
    "MAGI_CUSTOMIZE_LIFECYCLE_LLM_CALL_HOOKS_ENABLED",
    "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED",
    "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED",
    "MAGI_CUSTOMIZE_SESSION_TASK_EMITTERS_ENABLED",
    # F-QA4 / F-LIFE4b — the production helper consults
    # ``MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED`` (the
    # LIFECYCLE_-prefixed canonical name). The non-prefixed entry
    # above is kept for byte-identical OFF-path parity per the
    # F-QA3 conftest NOTE; both are flipped ON so the F-QA4
    # ``on_task_complete`` / ``on_session_start`` rows fire.
    "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED",
    # F-QA4 / F4 — capability_scope spawn-time subtraction master flag.
    "MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED",
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


class _SentinelCriticFactory:
    """Non-None sentinel returned by the patched critic-factory builders.

    ``_audit_one_rule`` only needs ``model_factory`` to be non-None to
    proceed past the ``"no critic model available"`` skip guard; the
    actual call into ``evaluate_criterion`` is intercepted by
    :class:`JudgePatcher`, so this sentinel is never actually invoked
    by the patched code path. The presence of this object is what
    flips the lifecycle gate verdict from ``"proceed"`` (skipped audit)
    to whatever verdict the patched judge returns.
    """

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return "<_SentinelCriticFactory: fqa-test stub>"


@pytest.fixture
def patched_critic_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> _SentinelCriticFactory:
    """Force the lifecycle critic-model factory builders to return non-None.

    ``magi_agent.runtime.governed_turn._build_lifecycle_critic_factory``
    (and the per-LLM-call sibling in
    ``magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory``)
    delegate to ``magi_agent.cli.wiring._build_criterion_model_factory``,
    which in turn calls ``_production_egress_critic_model_factory`` —
    a path that requires either a provider API key in the env or a
    provider entry in ``~/.magi/config.toml``. On a fresh-install / CI
    host neither is present, so the chain returns ``None``, the
    lifecycle ``_audit_one_rule`` short-circuits to
    ``status="skipped"`` (see ``model_factory is None`` guard around
    ``magi_agent/customize/lifecycle_audit.py:290``), and
    ``_gate_decision_from_audits`` reduces the worst verdict to
    ``"proceed"`` — collapsing every llm_criterion block-action row to
    a false pass.

    This fixture replaces the critic-factory builders with a constant
    that returns a sentinel object. The sentinel is non-None so the
    audit proceeds into ``evaluate_criterion``, which the sibling
    :func:`patched_judge` fixture intercepts. NOTE: the gate is wired
    such that ``model_factory`` MUST be non-None for it to fire —
    patching only ``evaluate_criterion`` is insufficient because the
    skip guard runs first.

    All three callsites that compose a factory inside the lifecycle
    audit fan-out are patched (governed_turn, lifecycle_llm_call_control,
    and the cli.wiring root) so every emitter slot — turn-boundary,
    user-prompt-submit, subagent-stop, and per-LLM-call — gets a
    non-None factory threaded through.
    """
    sentinel = _SentinelCriticFactory()

    def _stub_factory(*args: object, **kwargs: object) -> _SentinelCriticFactory:
        return sentinel

    # Root: the cli.wiring helper both lifecycle paths import.
    monkeypatch.setattr(
        "magi_agent.cli.wiring._build_criterion_model_factory",
        _stub_factory,
    )
    # Turn-boundary + user-prompt-submit + subagent-stop fan-outs.
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn._build_lifecycle_critic_factory",
        _stub_factory,
    )
    # Per-LLM-call (before_llm_call / after_llm_call) fan-out.
    monkeypatch.setattr(
        "magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory",
        _stub_factory,
    )
    return sentinel


@pytest.fixture
def patched_judge(
    monkeypatch: pytest.MonkeyPatch,
    patched_critic_factory: _SentinelCriticFactory,
) -> JudgePatcher:
    """Install a fake judge across the llm_criterion entry points.

    The same callable is patched in both the pre-final gate's import
    site (``magi_agent.customize.criterion_engine.evaluate_criterion``)
    and the after-tool gate's import site
    (``magi_agent.customize.after_tool_gate.evaluate_criterion``) so
    every llm_criterion code path resolves to the fake without a real
    provider round-trip.

    Auto-includes :func:`patched_critic_factory` so the lifecycle
    audit fan-out's ``model_factory is None`` skip guard does not
    short-circuit before the patched judge ever runs. Without that
    sibling fixture, every gate row on a key-less host would record
    ``status="skipped"`` and collapse to verdict=``"proceed"``.
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
