"""PR-E item 3 — the local ADK chat SSE seam threads a per-turn recall query
into prompt assembly, and ``build_cli_instruction`` injects a ``<memory-recall>``
block for it (gated, default-OFF, byte-identical when off).

Two proofs:
  1. WIRING: ``_local_adk_chat_sse`` passes the incoming user message as
     ``recall_query`` to ``build_headless_runtime``.
  2. E2E: ``build_cli_instruction`` — the call site that assembles the memory
     snapshot block — searches the workspace memory tree for that query and
     injects the matching hit as a ``<memory-recall>`` block when the gates are
     on; with the gates off the prompt is byte-identical (no block, no search).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# 1. WIRING: the SSE seam threads the user message as recall_query
# --------------------------------------------------------------------------


class _FakeEngine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")


async def _drain(gen) -> str:  # noqa: ANN001
    return "".join([chunk async for chunk in gen])


@pytest.mark.asyncio
async def test_sse_seam_threads_user_message_as_recall_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import magi_agent.cli.wiring as wiring
    from magi_agent.transport import chat as chat_mod

    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))

    captured: dict[str, object] = {}

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(engine=_FakeEngine(), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )

    runtime = SimpleNamespace(config=SimpleNamespace(model="anthropic/claude"))
    out = await _drain(
        chat_mod._local_adk_chat_sse(
            runtime, {"sessionId": "s", "turnId": "t"}, "what did we decide about zebraquux"
        )
    )
    assert out.rstrip().endswith("data: [DONE]")
    assert captured.get("recall_query") == "what did we decide about zebraquux"


# --------------------------------------------------------------------------
# 2. E2E: build_cli_instruction injects/omits the <memory-recall> block
# --------------------------------------------------------------------------


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_MEMORY_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_RECALL_ENABLED", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_LOCAL_SEARCH", "1")
    monkeypatch.setenv("MAGI_MEMORY_PREFER_QMD", "0")  # force pure-python BM25


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (
        "MAGI_MEMORY_ENABLED",
        "MAGI_MEMORY_RECALL_ENABLED",
        "MAGI_MEMORY_PREFER_LOCAL_SEARCH",
        "MAGI_MEMORY_PROJECTION_ENABLED",
        "MAGI_LEARNING_INJECTION_ENABLED",
    ):
        monkeypatch.delenv(env, raising=False)


def test_build_cli_instruction_injects_recall_block_when_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _on(monkeypatch)
    # WS2 PR2c: recall hits are deduped against the assembled snapshot (which
    # includes the projected recent ``memory/daily/*.md`` tail). To prove recall
    # WIRING here, surface the hit from a non-daily memory subdir (BM25 indexes
    # ``memory/**`` recursively; the projection only globs ``memory/daily/*.md``),
    # so it is recall-able but NOT already in the snapshot and survives dedup.
    _write(
        tmp_path,
        "memory/decisions/2026-06-01.md",
        "decision: adopt zebraquux for the billing rollout next sprint",
    )
    _write(tmp_path, "memory/daily/2026-06-02.md", "an unrelated grocery list note")

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="what did we decide about zebraquux",
    )
    assert "<memory-recall" in instruction
    assert "</memory-recall>" in instruction
    assert "zebraquux" in instruction
    # The unrelated grocery doc must not appear INSIDE the recall block (the
    # static <memory-context> snapshot may still list it — that's orthogonal).
    recall = instruction.split("<memory-recall")[1].split("</memory-recall>")[0]
    assert "zebraquux" in recall
    assert "grocery" not in recall


def test_recall_block_off_path_does_no_work_and_emits_no_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (gates off): the recall builder returns "" — no search runs and
    no block is produced, so the recall prepend branch is a guaranteed no-op
    (the instruction prefix is byte-identical to the no-recall_query path; the
    full prompt carries an unrelated wall-clock timestamp, so we assert on the
    block builder + the absence of the fence rather than the whole string)."""
    import magi_agent.cli.memory_recall_block as mod
    from magi_agent.cli.memory_recall_block import build_cli_memory_recall_block
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _off(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_PREFER_QMD", "0")
    _write(
        tmp_path,
        "memory/daily/2026-06-01.md",
        "decision: adopt zebraquux for the billing rollout",
    )

    # The search backend must never be consulted when the gates are off.
    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("search backend must not run when recall is off")

    monkeypatch.setattr(mod, "select_search_backend", _boom)

    assert (
        build_cli_memory_recall_block(
            workspace_root=str(tmp_path),
            query="what did we decide about zebraquux",
            memory_mode="normal",
        )
        == ""
    )

    with_query = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="what did we decide about zebraquux",
    )
    assert "<memory-recall" not in with_query


def test_recall_leads_snapshot_follows_when_both_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When BOTH a query-relevant recall block AND the static snapshot block are
    non-empty, the documented order holds: the recall leads, the snapshot
    follows, and both are present."""
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _on(monkeypatch)
    # Also turn the static <memory-context> snapshot on (default-OFF gate).
    monkeypatch.setenv("MAGI_MEMORY_PROJECTION_ENABLED", "1")
    # Snapshot source (curated MEMORY.md) carries a marker disjoint from the
    # recall query so each block's distinctive token is unambiguous.
    _write(tmp_path, "MEMORY.md", "curated snapshot marker snapshotonlymarker")
    # Recall source: a non-daily memory log matching the query term. WS2 PR2c
    # dedups recall hits against the assembled snapshot (which includes the
    # projected recent ``memory/daily/*.md`` tail), so a daily source here would
    # be deduped out; a ``memory/decisions/`` file is recall-able but NOT in the
    # snapshot, so both the recall AND the snapshot blocks coexist.
    _write(
        tmp_path,
        "memory/decisions/2026-06-01.md",
        "decision: adopt zebraquux for the billing rollout",
    )

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="what did we decide about zebraquux",
    )

    # Both blocks present.
    assert "<memory-recall" in instruction
    assert "<memory-context" in instruction
    assert "snapshotonlymarker" in instruction
    # Recall leads, snapshot follows.
    assert instruction.index("<memory-recall") < instruction.index("<memory-context")


def test_serve_recall_is_background_tagged_fenced_block_not_a_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: the SERVE per-turn recall must be a clearly BACKGROUND-tagged FENCED
    block — ``<memory-recall ... continuity="background">`` — and must NOT be
    shaped as a normal user/assistant conversation turn (no ADK role wrappers,
    no chat-turn markers around the recalled content)."""
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _on(monkeypatch)
    # WS2 PR2c: surface the recall hit from a non-daily memory subdir so it is
    # not deduped against the projected recent-daily tail of the snapshot.
    _write(
        tmp_path,
        "memory/decisions/2026-06-01.md",
        "decision: adopt zebraquux for the billing rollout next sprint",
    )

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="what did we decide about zebraquux",
    )

    # Fenced + background-tagged.
    assert '<memory-recall hidden="true" continuity="background">' in instruction
    assert "</memory-recall>" in instruction
    assert "zebraquux" in instruction

    # The recalled content lives strictly INSIDE the fence — never emitted as a
    # standalone conversation turn.
    recall = instruction.split('<memory-recall hidden="true" continuity="background">')[1]
    recall = recall.split("</memory-recall>")[0]
    assert "zebraquux" in recall
    # Not shaped as a user/assistant turn: no ADK content-role envelopes leaking
    # the recall as dialogue.
    for marker in ('"role": "user"', '"role": "assistant"', "role='user'", "role='assistant'"):
        assert marker not in recall


def test_serve_recall_continuity_policy_present_once_with_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: when BOTH the static snapshot AND the per-turn recall are present, the
    continuity-policy block PRECEDES the recalled memory and appears EXACTLY
    ONCE (no duplication between the snapshot preamble and the recall preamble)."""
    from magi_agent.cli.tool_runtime import build_cli_instruction
    from magi_agent.memory.continuity_policy import (
        MEMORY_CONTINUITY_POLICY_OPEN,
        build_continuity_policy_block,
    )

    _on(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_PROJECTION_ENABLED", "1")
    _write(tmp_path, "MEMORY.md", "curated snapshot marker snapshotonlymarker")
    # WS2 PR2c: non-daily recall source so it is not deduped against the snapshot.
    _write(
        tmp_path,
        "memory/decisions/2026-06-01.md",
        "decision: adopt zebraquux for the billing rollout",
    )

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="what did we decide about zebraquux",
    )

    # Both memory blocks present.
    assert "<memory-recall" in instruction
    assert "<memory-context" in instruction
    # Exactly one continuity-policy block — never duplicated.
    assert instruction.count(MEMORY_CONTINUITY_POLICY_OPEN) == 1
    assert instruction.count(build_continuity_policy_block()) == 1
    # The policy PRECEDES the recalled memory (background reference framing).
    assert instruction.index(MEMORY_CONTINUITY_POLICY_OPEN) < instruction.index(
        "<memory-recall"
    )


def test_serve_recall_continuity_policy_present_without_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: even when the static snapshot is OFF (so it emits no policy preamble),
    a present per-turn recall block is still led by the continuity-policy block
    exactly once — recalled memory is always framed as background reference."""
    from magi_agent.cli.tool_runtime import build_cli_instruction
    from magi_agent.memory.continuity_policy import MEMORY_CONTINUITY_POLICY_OPEN

    _on(monkeypatch)
    # Force the snapshot projection OFF explicitly: it FOLLOWS the master (now on
    # via _on), so a bare delenv would leave it on. With it off there is no
    # <memory-context> and no projected recent-daily tail to dedup against.
    monkeypatch.setenv("MAGI_MEMORY_PROJECTION_ENABLED", "0")
    _write(
        tmp_path,
        "memory/decisions/2026-06-01.md",
        "decision: adopt zebraquux for the billing rollout",
    )

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="what did we decide about zebraquux",
    )

    assert "<memory-recall" in instruction
    assert instruction.count(MEMORY_CONTINUITY_POLICY_OPEN) == 1
    assert instruction.index(MEMORY_CONTINUITY_POLICY_OPEN) < instruction.index(
        "<memory-recall"
    )


def test_serve_recall_incognito_emits_no_block_and_no_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2: incognito fully suppresses recall — no recall block, and (with the
    snapshot also incognito-suppressed) no orphan continuity-policy preamble."""
    from magi_agent.cli.tool_runtime import build_cli_instruction
    from magi_agent.memory.continuity_policy import MEMORY_CONTINUITY_POLICY_OPEN

    _on(monkeypatch)
    monkeypatch.setenv("MAGI_MEMORY_PROJECTION_ENABLED", "1")
    _write(tmp_path, "MEMORY.md", "curated snapshot marker")
    _write(tmp_path, "memory/daily/2026-06-01.md", "decision about zebraquux here")

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        memory_mode="incognito",
        recall_query="zebraquux",
    )
    assert "<memory-recall" not in instruction
    assert MEMORY_CONTINUITY_POLICY_OPEN not in instruction


def test_build_cli_instruction_recall_blocked_in_incognito(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _on(monkeypatch)
    _write(tmp_path, "memory/daily/2026-06-01.md", "decision about zebraquux here")

    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        memory_mode="incognito",
        recall_query="zebraquux",
    )
    assert "<memory-recall" not in instruction


def test_build_cli_instruction_recall_fail_soft_on_backend_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import magi_agent.cli.memory_recall_block as mod
    from magi_agent.cli.tool_runtime import build_cli_instruction

    _on(monkeypatch)
    _write(tmp_path, "memory/daily/2026-06-01.md", "decision about zebraquux here")

    class _Boom:
        def reindex(self, root: object, **kwargs: object) -> None:
            pass

        def search(self, query: str, *, k: int) -> object:
            raise RuntimeError("backend boom")

    monkeypatch.setattr(mod, "select_search_backend", lambda config: _Boom())

    # Must not raise; just no recall block.
    instruction = build_cli_instruction(
        session_id="s1",
        model="claude-sonnet-4-6",
        workspace_root=str(tmp_path),
        recall_query="zebraquux",
    )
    assert "<memory-recall" not in instruction
