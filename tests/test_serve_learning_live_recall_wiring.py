"""01-PR4 (C2) — wire the gated-live learning-recall/write harness into SERVE.

Before this PR both ``build_gated_live_learning_recall_harness``
(``harness/memory_recall.py``) and ``build_gated_live_learning_write_harness``
(``harness/memory_write.py``) had ZERO serve consumers — the live learning
recall/write harnesses were built and unit-tested at the factory level but no
serve/CLI prompt-assembly seam ever called them (the unified-rag B1 gap).

This PR threads them into ``build_cli_instruction`` (the serve prompt-assembly
seam that ``transport.chat._local_adk_chat_sse`` already routes through via
``build_headless_runtime``), behind the EXISTING learning-live readiness ladder
(``gates/learning_live_readiness``: env gate ``MAGI_LEARNING_LIVE_ENABLED`` +
a caller-PROVIDED readiness config carrying the selected-scope canary).

Spec PR4 default-state: *no new flags — consume the existing
``MAGI_LEARNING_LIVE_ENABLED`` + readiness config*.  So the serve seam never
reads any net-new ``MAGI_LEARNING_LIVE_*`` env var: it consumes a readiness
config the caller (the runtime/control-plane that already owns the canary
digests) resolves and threads down.  When no readiness config is provided
(the default CLI/local case) the live path stays ``disabled``:

* no readiness config (default) → ``disabled`` → factory returns ``None`` → no
  block, no recall/write, prompt byte-identical to pre-wiring;
* readiness ``live`` (provided + ``MAGI_LEARNING_LIVE_ENABLED=1``) → the gated
  factory binds a real harness, the serve seam runs recall and injects a
  ``<learning-live-recall>`` block from PUBLIC-SAFE projection snippets AND runs
  the write harness for a symmetric audit record;
* readiness ``shadow`` (gate ON but not canary-promoted) → no live binding →
  no block (observe-only is not a serve injection).

No ``Literal[False]`` authority flag is flipped — live behaviour is gate-derived.
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _live_readiness(*, bot_id: str, user_id: str):
    """A readiness config (caller-provided) that resolves to ``live``.

    Mirrors how the hosted control-plane builds the canary config — the serve
    seam consumes this object; it NEVER reads net-new env vars to assemble it.
    """
    from magi_agent.gates.learning_live_readiness import LearningLiveReadinessConfig

    return LearningLiveReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_digest(bot_id),
        selectedOwnerUserIdDigest=_digest(user_id),
        environment="staging",
        environmentAllowlist=("staging",),
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )


def _shadow_readiness(*, bot_id: str, user_id: str):
    """A readiness config that resolves to ``shadow`` (not canary-promoted)."""
    from magi_agent.gates.learning_live_readiness import LearningLiveReadinessConfig

    return LearningLiveReadinessConfig(
        enabled=True,
        killSwitchEnabled=False,
        shadowModeEnabled=True,
        selectedBotDigest=_digest(bot_id),
        selectedOwnerUserIdDigest=_digest(user_id),
        environment="staging",
        environmentAllowlist=("staging",),
        promotedGate=0,
        canaryPromotionConfirmed=False,
    )


def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn ON the EXISTING learning-live env gate (the only flag consulted)."""
    monkeypatch.setenv("MAGI_LEARNING_ENABLED", "1")
    monkeypatch.setenv("MAGI_LEARNING_LIVE_ENABLED", "1")


def _off_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (
        "MAGI_LEARNING_ENABLED",
        "MAGI_LEARNING_LIVE_ENABLED",
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
# 0. NO new public env-var surface (spec "no new flags")
# ---------------------------------------------------------------------------


def test_no_net_new_learning_live_env_var_surface() -> None:
    """The from-env readiness builder + its 9 net-new MAGI_LEARNING_LIVE_* env
    vars must NOT exist — spec PR4 promises no new flags, only the existing
    MAGI_LEARNING_LIVE_ENABLED + a caller-provided readiness config."""
    import magi_agent.gates.learning_live_readiness as gate

    assert not hasattr(gate, "build_learning_live_readiness_config_from_env")
    src = gate.__file__
    with open(src, "r", encoding="utf-8") as fh:
        text = fh.read()
    for forbidden in (
        "MAGI_LEARNING_LIVE_GATE_ENABLED",
        "MAGI_LEARNING_LIVE_KILL_SWITCH",
        "MAGI_LEARNING_LIVE_SHADOW_MODE",
        "MAGI_LEARNING_LIVE_SELECTED_BOT_DIGEST",
        "MAGI_LEARNING_LIVE_SELECTED_OWNER_DIGEST",
        "MAGI_LEARNING_LIVE_ENVIRONMENT",
        "MAGI_LEARNING_LIVE_ENVIRONMENT_ALLOWLIST",
        "MAGI_LEARNING_LIVE_PROMOTED_GATE",
        "MAGI_LEARNING_LIVE_CANARY_CONFIRMED",
    ):
        assert forbidden not in text, f"net-new flag {forbidden} reintroduced"


# ---------------------------------------------------------------------------
# 1. serve recall block builder — OFF/shadow → "" ; live → public-safe block
# ---------------------------------------------------------------------------


def test_serve_block_is_empty_when_no_readiness(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    # No readiness config provided (default CLI/local) → disabled → "".
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
            readiness=None,
        )
        == ""
    )


def test_serve_block_is_empty_when_env_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _off_env(monkeypatch)
    _seed_learning(tmp_path)
    # Readiness says live, but MAGI_LEARNING_LIVE_ENABLED is OFF (default) →
    # the resolver hard-short-circuits to disabled → "".
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
            readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
        )
        == ""
    )


def test_serve_block_injects_public_safe_snippet_when_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    block = build_serve_live_learning_recall_block(
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        memory_mode="normal",
        bot_id="bot-1",
        user_id="user-1",
        readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
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

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
            readiness=_shadow_readiness(bot_id="bot-1", user_id="user-1"),
        )
        == ""
    )


def test_serve_block_empty_when_identity_not_selected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Readiness selects bot-1/user-1 but the serve caller passes a DIFFERENT
    identity → scope mismatch → disabled → "".  This proves real identity is
    consumed (issue 3): the literal 'local' default would NOT match the canary
    digest, so passing it through must keep the path closed."""
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="some-other-bot",
            user_id="user-1",
            readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
        )
        == ""
    )


def test_serve_block_empty_in_incognito(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    assert (
        build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="incognito",
            bot_id="bot-1",
            user_id="user-1",
            readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
        )
        == ""
    )


# ---------------------------------------------------------------------------
# 2. serve WRITE audit symmetry (spec PR4 file-map: "write 대칭"; test (c))
# ---------------------------------------------------------------------------


def test_serve_write_audit_none_when_no_readiness(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_write_audit,
    )

    _live_env(monkeypatch)
    audit = build_serve_live_learning_write_audit(
        workspace_root=str(tmp_path),
        memory_mode="normal",
        bot_id="bot-1",
        user_id="user-1",
        readiness=None,
    )
    assert audit is None


def test_serve_write_audit_none_in_shadow(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_write_audit,
    )

    _live_env(monkeypatch)
    audit = build_serve_live_learning_write_audit(
        workspace_root=str(tmp_path),
        memory_mode="normal",
        bot_id="bot-1",
        user_id="user-1",
        readiness=_shadow_readiness(bot_id="bot-1", user_id="user-1"),
    )
    assert audit is None


def test_serve_write_audit_emitted_when_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live path runs the gated WRITE harness → a public-safe audit record.

    Authority flags stay frozen-False — the audit proves the seam reached the
    write harness, not that any Literal[False] flag flipped."""
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_write_audit,
    )

    _live_env(monkeypatch)
    audit = build_serve_live_learning_write_audit(
        workspace_root=str(tmp_path),
        memory_mode="normal",
        bot_id="bot-1",
        user_id="user-1",
        readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
    )
    assert audit is not None
    # public-safe projection: status + reasonCodes + authorityFlags present.
    assert "status" in audit
    assert "authorityFlags" in audit
    # every authority flag stays False (frozen Literal[False]).
    assert all(value is False for value in audit["authorityFlags"].values())


def test_serve_write_audit_none_in_incognito(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_write_audit,
    )

    _live_env(monkeypatch)
    audit = build_serve_live_learning_write_audit(
        workspace_root=str(tmp_path),
        memory_mode="incognito",
        bot_id="bot-1",
        user_id="user-1",
        readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
    )
    assert audit is None


# ---------------------------------------------------------------------------
# 3. build_cli_instruction (the serve prompt-assembly seam) injects/omits
# ---------------------------------------------------------------------------


def test_cli_instruction_injects_live_learning_block_when_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        bot_id="bot-1",
        user_id="user-1",
        learning_live_readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
    )
    assert "<learning-live-recall" in instruction
    assert "zebraquux" in instruction


def test_cli_instruction_byte_identical_when_no_readiness(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (no readiness config threaded): no recall runs, the prompt carries
    no <learning-live-recall> fence — byte-identical to the pre-wiring prompt."""
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _live_env(monkeypatch)
    _seed_learning(tmp_path)
    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
        bot_id="bot-1",
        user_id="user-1",
        learning_live_readiness=None,
    )
    assert "<learning-live-recall" not in instruction


def test_cli_instruction_byte_identical_when_env_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Readiness provided but MAGI_LEARNING_LIVE_ENABLED OFF (default) → the
    resolver short-circuits to disabled → no fence → byte-identical."""
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
        learning_live_readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
    )
    assert "<learning-live-recall" not in instruction


def test_cli_instruction_live_block_fail_soft_on_recall_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recall failure must never break prompt assembly — block is just omitted."""
    import magi_agent.cli.learning_recall as mod
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _live_env(monkeypatch)
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
        learning_live_readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
    )
    assert "<learning-live-recall" not in instruction


# ---------------------------------------------------------------------------
# 3b. REAL serve async call context (issue 1/2) — the builders run inside a
#     RUNNING event loop on the hosted/serve path
#     (``transport.chat._local_adk_chat_sse`` is ``async`` and calls
#     ``build_headless_runtime`` → ``build_cli_instruction`` → these builders
#     DIRECTLY on-loop, NOT via ``to_thread``).  The prior tests called the
#     builders synchronously (no loop) so ``asyncio.run`` succeeded there and
#     hid the production break.  These tests reproduce the running-loop site:
#     an ``asyncio.run()`` inside an already-running loop raises
#     ``RuntimeError`` which the broad ``except`` swallowed → empty block / no
#     audit → the feature was silently inert exactly on the path this PR exists
#     to serve.
# ---------------------------------------------------------------------------


def test_serve_block_injects_snippet_when_live_inside_running_loop(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven from inside ``asyncio.run`` — mirrors the real serve call site.

    Before the fix this returned ``""`` (asyncio.run-in-running-loop
    RuntimeError, swallowed); after the fix the block is injected just like
    the synchronous case.
    """
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_recall_block,
    )

    _live_env(monkeypatch)
    _seed_learning(tmp_path)

    async def _drive() -> str:
        # Synchronous builder invoked from within a running loop, exactly as
        # build_cli_instruction is invoked on-loop by _local_adk_chat_sse.
        return build_serve_live_learning_recall_block(
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
            readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
        )

    block = asyncio.run(_drive())
    assert "<learning-live-recall" in block
    assert "</learning-live-recall>" in block
    assert "zebraquux" in block


def test_serve_write_audit_emitted_when_live_inside_running_loop(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write-audit symmetry under a running loop — the real serve context."""
    from magi_agent.cli.learning_recall import (
        build_serve_live_learning_write_audit,
    )

    _live_env(monkeypatch)

    async def _drive() -> object:
        return build_serve_live_learning_write_audit(
            workspace_root=str(tmp_path),
            memory_mode="normal",
            bot_id="bot-1",
            user_id="user-1",
            readiness=_live_readiness(bot_id="bot-1", user_id="user-1"),
        )

    audit = asyncio.run(_drive())
    assert audit is not None
    assert "status" in audit
    assert "authorityFlags" in audit
    assert all(value is False for value in audit["authorityFlags"].values())


def test_cli_instruction_injects_live_block_inside_running_loop(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: ``build_cli_instruction`` (the on-loop serve seam) must inject
    the live block when invoked from within a running loop, not just when called
    synchronously from a test with no loop."""
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _live_env(monkeypatch)
    _seed_learning(tmp_path)

    async def _drive() -> str:
        return build_cli_instruction(
            session_id="s1",
            model="claude-sonnet-4-6",
            workspace_root=str(tmp_path),
            recall_query="zebraquux",
            bot_id="bot-1",
            user_id="user-1",
            learning_live_readiness=_live_readiness(
                bot_id="bot-1", user_id="user-1"
            ),
        )

    instruction = asyncio.run(_drive())
    assert "<learning-live-recall" in instruction
    assert "zebraquux" in instruction


# ---------------------------------------------------------------------------
# 4. serve seam threads REAL identity (issue 3) end-to-end through wiring
# ---------------------------------------------------------------------------


def test_build_cli_model_runner_forwards_identity_to_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """real_runner.build_cli_model_runner must forward bot_id/user_id (and the
    readiness config) to build_cli_instruction — otherwise the serve path always
    sees the literal 'local' default and the canary digest can never match."""
    import magi_agent.cli.real_runner as rr

    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> str:
        seen.update(kwargs)
        return "fake-instruction"

    # Patch build_cli_instruction at the import site used inside the function.
    import magi_agent.cli.tool_runtime as tr

    monkeypatch.setattr(tr, "build_cli_instruction", _capture)

    readiness = _live_readiness(bot_id="bot-9", user_id="user-9")

    from magi_agent.cli.providers import ProviderConfig

    cfg = ProviderConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="x",
    )

    rr.build_cli_model_runner(
        cfg,
        # ADK Agent accepts a string model id; return one so construction
        # succeeds past the instruction-capture point.
        model_factory=lambda _c: "anthropic/claude-sonnet-4-6",
        tools=[],
        workspace_root="/tmp/ws",
        recall_query="hi",
        bot_id="bot-9",
        owner_user_id="user-9",
        learning_live_readiness=readiness,
    )

    assert seen.get("bot_id") == "bot-9"
    # owner identity (not the ADK session user) is what reaches the canary digest.
    assert seen.get("user_id") == "user-9"
    assert seen.get("learning_live_readiness") is readiness
