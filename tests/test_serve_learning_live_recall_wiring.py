"""01-PR4 (C2) — wire the gated-live learning-recall harness into the SERVE path.

Before this PR ``build_gated_live_learning_recall_harness``
(``harness/memory_recall.py``) had ZERO serve consumers — the live learning
recall harness was built and unit-tested at the factory level but no serve/CLI
prompt-assembly seam ever called it (the unified-rag B1 gap).

This PR threads it into ``build_cli_instruction`` (the serve prompt-assembly
seam that ``transport.chat._local_adk_chat_sse`` already routes through via
``build_headless_runtime``), behind the EXISTING learning-live readiness ladder
(``gates/learning_live_readiness``: env gate ``MAGI_LEARNING_LIVE_ENABLED`` +
selected-scope canary).  Default-OFF is PRESERVED, not flipped on:

* env gate OFF (default) → readiness resolves ``disabled`` → the gated factory
  returns ``None`` → no block, no recall, prompt byte-identical to pre-wiring;
* env gate ON + readiness ``live`` → the gated factory binds a real harness, the
  serve seam runs recall and injects a ``<learning-live-recall>`` block built
  from the PUBLIC-SAFE sanitized projection snippets;
* readiness ``shadow`` (gate ON but not canary-promoted) → no live binding →
  no block (observe-only is not a serve injection).

No ``Literal[False]`` authority flag is flipped — live behaviour is gate-derived.
"""
from __future__ import annotations

import hashlib

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _live_env(monkeypatch: pytest.MonkeyPatch, *, bot_id: str, user_id: str) -> None:
    """Set the env so the from-env readiness builder resolves to ``live``."""
    monkeypatch.setenv("MAGI_LEARNING_ENABLED", "1")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_ENABLED", "1")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_KILL_SWITCH", "0")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_SHADOW_MODE", "1")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_SELECTED_BOT_DIGEST", _digest(bot_id))
    monkeypatch.setenv(
        "MAGI_LEARNING_LIVE_SELECTED_OWNER_DIGEST", _digest(user_id)
    )
    monkeypatch.setenv("MAGI_LEARNING_LIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_ENVIRONMENT_ALLOWLIST", "staging")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_PROMOTED_GATE", "5")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_CANARY_CONFIRMED", "1")


def _off_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (
        "MAGI_LEARNING_ENABLED",
        "MAGI_LEARNING_LIVE_ENABLED",
        "MAGI_LEARNING_LIVE_GATE_ENABLED",
        "MAGI_LEARNING_LIVE_SHADOW_MODE",
        "MAGI_LEARNING_LIVE_SELECTED_BOT_DIGEST",
        "MAGI_LEARNING_LIVE_SELECTED_OWNER_DIGEST",
        "MAGI_LEARNING_LIVE_ENVIRONMENT",
        "MAGI_LEARNING_LIVE_ENVIRONMENT_ALLOWLIST",
        "MAGI_LEARNING_LIVE_PROMOTED_GATE",
        "MAGI_LEARNING_LIVE_CANARY_CONFIRMED",
        "MAGI_LEARNING_INJECTION_ENABLED",
    ):
        monkeypatch.delenv(env, raising=False)


def _seed_learning(tmp_path) -> None:
    """Persist an active learning item the recall harness can surface.

    Uses the real eval-gate auto-activation path (``example`` + passing
    checkset) so the item lands ``active`` via the supported pipeline — the
    store forbids writing ``status="active"`` directly.
    """
    from magi_agent.learning.candidates import LearningCandidate
    from magi_agent.learning.eval_gate import StaticCheckSet, run_eval_gate
    from magi_agent.learning.models import LearningScope, Provenance
    from magi_agent.learning.store import (
        DEFAULT_LEARNING_DB_PATH,
        SqliteLearningStore,
    )

    store = SqliteLearningStore(
        db_path=DEFAULT_LEARNING_DB_PATH, workspace_root=str(tmp_path)
    )
    try:
        candidate = LearningCandidate(
            kind="example",
            scope=LearningScope(taskKind="general", tags=("style",)),
            content={
                "situation": "user asks",
                "behavior": "prefer concise zebraquux summaries when answering",
            },
            rationale="prefer concise zebraquux summaries when answering",
            provenance=Provenance(
                sessionIds=("sess-1",),
                derivedBy="reflection",
                createdAt="2026-06-03T10:00:00Z",
            ),
            sourceSignalRef="signal:diff@sess-1",
        )
        run_eval_gate(
            (candidate,),
            store=store,
            checkset=StaticCheckSet(
                before=(1.0, 1.0, 1.0, 1.0), after=(1.0, 1.0, 1.0, 1.0)
            ),
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 1. from-env readiness builder
# ---------------------------------------------------------------------------


def test_readiness_from_env_resolves_live_when_fully_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.gates.learning_live_readiness import (
        build_learning_live_readiness_config_from_env,
        resolve_learning_live_execution_mode,
    )

    _live_env(monkeypatch, bot_id="bot-1", user_id="user-1")
    cfg = build_learning_live_readiness_config_from_env()
    assert (
        resolve_learning_live_execution_mode(cfg, bot_id="bot-1", user_id="user-1")
        == "live"
    )
    # The locked authority flag is never granted from env.
    assert cfg.live_authority_allowed is False


def test_readiness_from_env_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.gates.learning_live_readiness import (
        build_learning_live_readiness_config_from_env,
        resolve_learning_live_execution_mode,
    )

    _off_env(monkeypatch)
    cfg = build_learning_live_readiness_config_from_env()
    assert (
        resolve_learning_live_execution_mode(cfg, bot_id="bot-1", user_id="user-1")
        == "disabled"
    )


# ---------------------------------------------------------------------------
# 2. serve block builder — OFF/shadow → "" ; live → public-safe block
# ---------------------------------------------------------------------------


def test_serve_block_is_empty_when_env_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _off_env(monkeypatch)
    _seed_learning(tmp_path)
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
        )
        == ""
    )


def test_serve_block_injects_public_safe_snippet_when_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch, bot_id="bot-1", user_id="user-1")
    _seed_learning(tmp_path)
    block = build_serve_live_learning_recall_block(
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        memory_mode="normal",
        bot_id="bot-1",
        user_id="user-1",
    )
    assert "<learning-live-recall" in block
    assert "</learning-live-recall>" in block
    assert "zebraquux" in block


def test_serve_block_empty_in_shadow_mode(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    # Live env but NOT canary-promoted → readiness resolves shadow → no binding.
    _live_env(monkeypatch, bot_id="bot-1", user_id="user-1")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_PROMOTED_GATE", "0")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_CANARY_CONFIRMED", "0")
    _seed_learning(tmp_path)
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
        )
        == ""
    )


def test_serve_block_empty_in_incognito(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch, bot_id="bot-1", user_id="user-1")
    _seed_learning(tmp_path)
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="incognito",
            bot_id="bot-1",
            user_id="user-1",
        )
        == ""
    )


# ---------------------------------------------------------------------------
# 3. build_cli_instruction (the serve prompt-assembly seam) injects/omits
# ---------------------------------------------------------------------------


def test_cli_instruction_injects_live_learning_block_when_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _live_env(monkeypatch, bot_id="bot-1", user_id="user-1")
    _seed_learning(tmp_path)
    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        bot_id="bot-1",
        user_id="user-1",
    )
    assert "<learning-live-recall" in instruction
    assert "zebraquux" in instruction


def test_cli_instruction_byte_identical_when_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OFF (default): the gated factory returns None, no recall runs, and the
    prompt carries no <learning-live-recall> fence — byte-identical to the
    pre-wiring serve prompt."""
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _off_env(monkeypatch)
    _seed_learning(tmp_path)
    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        bot_id="bot-1",
        user_id="user-1",
    )
    assert "<learning-live-recall" not in instruction


def test_cli_instruction_live_block_fail_soft_on_recall_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recall failure must never break prompt assembly — block is just omitted."""
    import magi_agent.cli.learning_recall as mod
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _live_env(monkeypatch, bot_id="bot-1", user_id="user-1")
    _seed_learning(tmp_path)

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("recall boom")

    monkeypatch.setattr(mod, "_run_serve_live_recall", _boom)

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        bot_id="bot-1",
        user_id="user-1",
    )
    assert "<learning-live-recall" not in instruction
