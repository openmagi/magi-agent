from __future__ import annotations

import os
from pathlib import Path

import pytest

from magi_agent.cli.contracts import RuntimeEvent
from magi_agent.cli.session_log import (
    DEFAULT_SESSION_ROOT_ENV,
    Envelope,
    ResumeContext,
    SessionLog,
    continue_latest,
    load,
    prepare_resume,
    reconstruct_linear_chain,
    reconstruct_messages,
    resolve_session_path,
    resume,
    slugify_cwd,
)


def _event(i: int) -> RuntimeEvent:
    return RuntimeEvent(
        type="status",
        payload={"n": i, "msg": f"event-{i}"},
        turn_id=f"turn-{i}",
    )


# 1. append -> reload round-trip ------------------------------------------------
def test_append_reload_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    log = SessionLog(path)
    n = 5
    for i in range(n):
        log.append(_event(i))
    log.close()

    envelopes = load(path)
    assert len(envelopes) == n
    for i, env in enumerate(envelopes):
        assert isinstance(env, Envelope)
        assert env.type == "status"
        assert env.payload == {"n": i, "msg": f"event-{i}"}
        assert env.turn_id == f"turn-{i}"
        assert isinstance(env.uuid, str) and env.uuid
        assert isinstance(env.ts, float)


# 2. parent_uuid chain correctness ---------------------------------------------
def test_parent_uuid_linear_chain(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    log = SessionLog(path)
    for i in range(4):
        log.append(_event(i))
    log.close()

    envelopes = load(path)
    assert envelopes[0].parent_uuid is None
    for prev, cur in zip(envelopes, envelopes[1:]):
        assert cur.parent_uuid == prev.uuid


def test_explicit_parent_forks_dag_branch(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    log = SessionLog(path)
    root_uuid = log.append(_event(0))
    child_uuid = log.append(_event(1))  # chains off root
    # Fork a new branch from the root (rewind), not from `child`.
    branch_uuid = log.append(_event(2), parent_uuid=root_uuid)
    log.close()

    envelopes = load(path)
    by_uuid = {e.uuid: e for e in envelopes}
    assert by_uuid[root_uuid].parent_uuid is None
    assert by_uuid[child_uuid].parent_uuid == root_uuid
    assert by_uuid[branch_uuid].parent_uuid == root_uuid
    # The branch did NOT chain off the most-recently-appended node.
    assert by_uuid[branch_uuid].parent_uuid != child_uuid


# 3. batched flush still durable on close --------------------------------------
def test_batched_flush_durable_on_close(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    # Large flush interval so the batch is NOT flushed during appends.
    log = SessionLog(path, flush_interval_s=3600.0)
    for i in range(10):
        log.append(_event(i))  # no explicit flush
    log.close()

    envelopes = load(path)
    assert len(envelopes) == 10
    assert [e.payload["n"] for e in envelopes] == list(range(10))


def test_explicit_flush_persists_before_close(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    log = SessionLog(path, flush_interval_s=3600.0)
    log.append(_event(0))
    log.flush()
    assert len(load(path)) == 1  # visible before close
    log.close()
    assert len(load(path)) == 1


# 4. blank / partial trailing line skipped -------------------------------------
def test_load_skips_blank_and_partial_trailing_line(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    log = SessionLog(path)
    log.append(_event(0))
    log.append(_event(1))
    log.close()

    # Append a blank line and a half-written (invalid JSON) trailing line.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write('{"uuid": "x", "parent_uuid": null, "ts": 1.0, ')  # truncated

    envelopes = load(path)
    assert len(envelopes) == 2
    assert [e.payload["n"] for e in envelopes] == [0, 1]


def test_load_nonexistent_file_returns_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "missing.jsonl") == []


# 5. path resolution -----------------------------------------------------------
def test_session_dir_env_override_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    path = resolve_session_path("bot-1", "sess-1", cwd="/work/project")
    assert str(path).startswith(str(tmp_path))
    assert path.parent.parent == tmp_path / "projects"
    assert path.name == "bot-1__sess-1.jsonl"


def test_slugify_cwd_stable_and_safe() -> None:
    a = slugify_cwd("/Users/kevin/Desktop/my project")
    b = slugify_cwd("/Users/kevin/Desktop/my project")
    assert a == b
    assert "/" not in a and " " not in a
    assert a == "Users-kevin-Desktop-my-project"
    assert slugify_cwd("/") == "root"


def test_session_log_derives_path_from_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    log = SessionLog(bot_id="bot-2", session_id="sess-2", cwd="/work")
    log.append(_event(0))
    log.close()
    expected = resolve_session_path("bot-2", "sess-2", cwd="/work")
    assert log.path == expected
    assert os.path.exists(expected)
    assert len(load(expected)) == 1


def test_session_log_requires_path_or_session_id() -> None:
    with pytest.raises(ValueError):
        SessionLog()


# 6. post-close behavior -------------------------------------------------------
def test_append_after_close_raises(tmp_path: Path) -> None:
    log = SessionLog(tmp_path / "s.jsonl")
    log.append(_event(0))
    log.close()
    with pytest.raises(ValueError):
        log.append(_event(1))


def test_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    log = SessionLog(path)
    log.append(_event(0))
    log.close()
    log.close()  # second close must be a harmless no-op
    assert len(load(path)) == 1


# =============================================================================
# PR-B2: resume / continue
# =============================================================================


def _user_event(text: str, turn: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="status",
        payload={"type": "user_message", "content": text},
        turn_id=turn,
    )


def _text_delta_event(delta: str, turn: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="token",
        payload={"type": "text_delta", "delta": delta},
        turn_id=turn,
    )


def _turn_end_event(turn: str, status: str = "committed") -> RuntimeEvent:
    return RuntimeEvent(
        type="status",
        payload={"type": "turn_end", "turnId": turn, "status": status},
        turn_id=turn,
    )


def _write_multi_turn_log(path: Path, turns: list[tuple[str, list[str]]]) -> None:
    """Write a multi-turn log: each turn = user msg + assistant deltas + turn_end."""

    log = SessionLog(path)
    for idx, (user_text, assistant_deltas) in enumerate(turns):
        tid = f"turn-{idx}"
        log.append(_user_event(user_text, tid))
        for delta in assistant_deltas:
            log.append(_text_delta_event(delta, tid))
        log.append(_turn_end_event(tid))
    log.close()


# 1. multi-turn reconstruction -> exact ordered message list -------------------
def test_resume_reconstructs_exact_message_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    path = resolve_session_path("", "sess-multi", cwd="/work/proj")
    _write_multi_turn_log(
        path,
        [
            ("hello", ["Hi", " there"]),
            ("how are you?", ["I am", " fine", " thanks"]),
        ],
    )

    ctx = resume("sess-multi", cwd="/work/proj")
    assert isinstance(ctx, ResumeContext)
    assert ctx.session_id == "sess-multi"
    assert ctx.initial_messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "how are you?"},
        {"role": "assistant", "content": "I am fine thanks"},
    ]


def test_reconstruct_messages_pure_no_adk(tmp_path: Path) -> None:
    # reconstruct_linear_chain + reconstruct_messages are pure (no ADK, no env).
    path = tmp_path / "s.jsonl"
    _write_multi_turn_log(path, [("q1", ["a", "b"]), ("q2", ["c"])])
    chain = reconstruct_linear_chain(load(path))
    msgs = reconstruct_messages(chain)
    assert msgs == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "ab"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "c"},
    ]


# 2. continue_latest picks the most-recently-modified session ------------------
def test_continue_latest_picks_newest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    cwd = "/work/proj"
    older = resolve_session_path("botX", "old-sess", cwd=cwd)
    newer = resolve_session_path("botX", "new-sess", cwd=cwd)

    log_old = SessionLog(older)
    log_old.append(_user_event("old", "t0"))
    log_old.close()
    log_new = SessionLog(newer)
    log_new.append(_user_event("new", "t0"))
    log_new.close()

    # Force a deterministic mtime ordering (newer is more recent).
    os.utime(older, (1_000.0, 1_000.0))
    os.utime(newer, (2_000.0, 2_000.0))

    assert continue_latest("botX", cwd=cwd) == "new-sess"


def test_continue_latest_none_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    assert continue_latest("botX", cwd="/no/such/proj") is None


# 3. branched DAG resolves to the intended tip ---------------------------------
def test_branched_dag_resolves_to_latest_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    path = resolve_session_path("", "sess-branch", cwd="/work/proj")

    log = SessionLog(path)
    # Turn 0 (shared root path): user + assistant + turn_end.
    log.append(_user_event("root question", "turn-0"))
    log.append(_text_delta_event("root answer", "turn-0"))
    root_end = log.append(_turn_end_event("turn-0"))

    # Abandoned branch off root_end.
    log.append(_user_event("ABANDONED", "turn-abandoned"))
    log.append(_text_delta_event("abandoned answer", "turn-abandoned"))
    log.append(_turn_end_event("turn-abandoned"))

    # Intended branch: explicitly fork from root_end (rewind past the abandoned
    # branch). This is the most-recently-appended leaf, so it is the tip.
    keep_user = log.append(
        _user_event("KEPT", "turn-keep"), parent_uuid=root_end
    )
    log.append(_text_delta_event("kept answer", "turn-keep"), parent_uuid=keep_user)
    log.close()

    ctx = resume("sess-branch", cwd="/work/proj")
    contents = [m["content"] for m in ctx.initial_messages]
    assert "KEPT" in contents
    assert "kept answer" in contents
    assert "ABANDONED" not in contents
    assert "abandoned answer" not in contents
    # The shared root prefix is preserved on the kept branch.
    assert ctx.initial_messages[0] == {"role": "user", "content": "root question"}


# 4. prepare_resume with continue_-style args ----------------------------------
class _Args:
    def __init__(self, **kw: object) -> None:
        for key, value in kw.items():
            setattr(self, key, value)


def test_prepare_resume_continue_resolves_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    cwd = "/work/proj"
    sess_a = resolve_session_path("botY", "sess-a", cwd=cwd)
    sess_b = resolve_session_path("botY", "sess-b", cwd=cwd)
    _write_multi_turn_log(sess_a, [("a", ["A"])])
    _write_multi_turn_log(sess_b, [("b", ["B"])])
    os.utime(sess_a, (1_000.0, 1_000.0))
    os.utime(sess_b, (2_000.0, 2_000.0))

    args = _Args(continue_=True, bot_id="botY", cwd=cwd)
    ctx = prepare_resume(args)
    assert ctx.session_id == "sess-b"
    assert ctx.initial_messages == [
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "B"},
    ]


def test_prepare_resume_explicit_session_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    cwd = "/work/proj"
    path = resolve_session_path("", "explicit-sess", cwd=cwd)
    _write_multi_turn_log(path, [("explicit", ["E"])])

    args = _Args(resume="explicit-sess", cwd=cwd)
    ctx = prepare_resume(args)
    assert ctx.session_id == "explicit-sess"
    assert ctx.initial_messages[0] == {"role": "user", "content": "explicit"}


def test_prepare_resume_continue_no_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    args = _Args(continue_=True, bot_id="botY", cwd="/empty/proj")
    ctx = prepare_resume(args)
    assert ctx.session_id == ""
    assert ctx.initial_messages == []
    assert ctx.reason == "no_session_to_continue"


def test_prepare_resume_no_request(tmp_path: Path) -> None:
    ctx = prepare_resume(_Args())
    assert ctx.session_id == ""
    assert ctx.reason == "no_session_requested"


# 5. resume of nonexistent / empty session is empty-but-valid ------------------
def test_resume_nonexistent_session_is_empty_but_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    ctx = resume("does-not-exist", cwd="/work/proj")
    assert isinstance(ctx, ResumeContext)
    assert ctx.session_id == "does-not-exist"
    assert ctx.initial_messages == []
    assert ctx.session is None
    assert ctx.session_service is None
    assert ctx.continuity_result is None
    assert ctx.reason == "empty_session"


# DAG reconstruction unit edge cases -------------------------------------------
def test_reconstruct_linear_chain_empty() -> None:
    assert reconstruct_linear_chain([]) == []


def test_reconstruct_linear_chain_cycle_guard() -> None:
    # Two envelopes that point at each other: must not loop forever.
    a = Envelope(uuid="A", parent_uuid="B", ts=1.0, type="status", payload={}, turn_id="t")
    b = Envelope(uuid="B", parent_uuid="A", ts=2.0, type="status", payload={}, turn_id="t")
    chain = reconstruct_linear_chain([a, b])
    # Cyclic graph has no true leaf; tip falls back to last appended ("B"),
    # walk visits B then A and stops on the repeat.
    uuids = [e.uuid for e in chain]
    assert set(uuids) <= {"A", "B"}
    assert len(uuids) == len(set(uuids))  # no repeats


def test_reconstruct_messages_skips_tool_events(tmp_path: Path) -> None:
    # Tool events between user and assistant text must not corrupt the message
    # reconstruction (they are not text/user payloads).
    path = tmp_path / "s.jsonl"
    log = SessionLog(path)
    log.append(_user_event("do a thing", "t0"))
    log.append(_text_delta_event("working", "t0"))
    log.append(
        RuntimeEvent(
            type="tool",
            payload={"type": "tool_start", "id": "call-1", "name": "shell"},
            turn_id="t0",
        )
    )
    log.append(
        RuntimeEvent(
            type="tool",
            payload={"type": "tool_end", "id": "call-1", "status": "ok"},
            turn_id="t0",
        )
    )
    log.append(_text_delta_event(" done", "t0"))
    log.append(_turn_end_event("t0"))
    log.close()

    msgs = reconstruct_messages(reconstruct_linear_chain(load(path)))
    assert msgs == [
        {"role": "user", "content": "do a thing"},
        {"role": "assistant", "content": "working done"},
    ]


# Rehydration hand-off (ADK-dependent; skipped if google-adk unavailable) ------
def test_resume_rehydration_handoff_when_adk_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("google.adk")
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    path = resolve_session_path("", "sess-adk", cwd="/work/proj")
    _write_multi_turn_log(path, [("hello", ["Hi", " there"])])

    ctx = resume("sess-adk", cwd="/work/proj")
    # Message list always reconstructed.
    assert ctx.initial_messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    # With a committed turn_end the rehydration hand-off succeeds: a session +
    # continuity result are produced and no failure reason is set.
    assert ctx.reason is None
    assert ctx.session is not None
    assert ctx.session_service is not None
    assert ctx.continuity_result is not None


# Forward-map correctness WITHOUT google-adk (stub deps) -----------------------
#
# The ADK-gated test above only asserts the hand-off RAN. These tests pin the
# forward map (_envelopes_to_transcript_entries) directly with stub constructors
# so a kwarg/Literal break is caught even when google-adk is not installed in CI.
from magi_agent.cli.session_log import (  # noqa: E402
    _envelopes_to_transcript_entries,
)


class _Rec:
    def __init__(self, kind: str, kw: dict) -> None:
        self.kind = kind
        self.kw = kw


def _stub_deps() -> dict:
    names = [
        "TurnStartedEntry",
        "AssistantTextEntry",
        "ToolCallEntry",
        "ToolResultEntry",
        "TurnCommittedEntry",
        "TurnAbortedEntry",
        "ControlEventTranscriptEntry",
        # unused by the map but present in real deps:
        "WorkspaceSessionService",
        "SessionContinuityBoundary",
        "SessionContinuityConfig",
    ]
    return {name: (lambda *, _k=name, **kw: _Rec(_k, kw)) for name in names}


def _env(payload: dict, turn: str = "t0", uid: str = "u", parent=None) -> Envelope:
    return Envelope(
        uuid=uid, parent_uuid=parent, ts=1.0, type="status", payload=payload, turn_id=turn
    )


def test_forward_map_produces_expected_entries_with_correct_kwargs() -> None:
    chain = [
        _env({"type": "turn_start", "declaredRoute": "subagent"}, uid="a"),
        _env({"type": "text_delta", "delta": "Hi"}, uid="b", parent="a"),
        _env(
            {"type": "tool_start", "id": "call-1", "name": "Grep", "input_preview": {"q": 1}},
            uid="c",
            parent="b",
        ),
        _env(
            {"type": "tool_end", "id": "call-1", "status": "ok", "output_preview": "out"},
            uid="d",
            parent="c",
        ),
        _env({"type": "turn_end", "status": "committed"}, uid="e", parent="d"),
    ]
    entries, dropped, first_error = _envelopes_to_transcript_entries(chain, _stub_deps())

    assert dropped == 0 and first_error is None
    kinds = [e.kind for e in entries]
    assert kinds == [
        "TurnStartedEntry",
        "AssistantTextEntry",
        "ToolCallEntry",
        "ToolResultEntry",
        "TurnCommittedEntry",
    ]
    assert entries[0].kw["declaredRoute"] == "subagent"
    assert entries[1].kw["text"] == "Hi"
    assert entries[2].kw["toolUseId"] == "call-1" and entries[2].kw["name"] == "Grep"
    assert entries[3].kw["toolUseId"] == "call-1" and entries[3].kw["isError"] is False
    assert entries[4].kw["inputTokens"] == 0 and entries[4].kw["outputTokens"] == 0


def test_forward_map_aborted_turn_maps_to_aborted_entry() -> None:
    chain = [_env({"type": "turn_end", "status": "aborted", "reason": "user"}, uid="a")]
    entries, _dropped, _err = _envelopes_to_transcript_entries(chain, _stub_deps())
    assert [e.kind for e in entries] == ["TurnAbortedEntry"]
    assert entries[0].kw["reason"] == "user"


def test_forward_map_counts_drops_on_systematic_constructor_break() -> None:
    # Simulate a kwarg/Literal break: AssistantTextEntry construction always raises.
    deps = _stub_deps()

    def _boom(**_kw):  # noqa: ANN003
        raise TypeError("unexpected kwarg 'text'")

    deps["AssistantTextEntry"] = _boom
    chain = [
        _env({"type": "text_delta", "delta": "Hi"}, uid="a"),
        _env({"type": "turn_end", "status": "committed"}, uid="b", parent="a"),
    ]
    entries, dropped, first_error = _envelopes_to_transcript_entries(chain, deps)
    # The text entry was dropped (counted); the committed entry still built.
    assert dropped == 1
    assert first_error is not None and "TypeError" in first_error
    assert [e.kind for e in entries] == ["TurnCommittedEntry"]


# DAG edge cases: disjoint roots + dangling parent -----------------------------
def test_reconstruct_disjoint_roots_resolves_to_last_chain() -> None:
    # Two independent roots (no shared lineage). The most-recently-appended
    # chain (root2->leaf2) wins; the first chain is dropped.
    envelopes = [
        _env({"type": "text_delta", "delta": "A"}, uid="r1", parent=None),
        _env({"type": "text_delta", "delta": "B"}, uid="r1c", parent="r1"),
        _env({"type": "text_delta", "delta": "C"}, uid="r2", parent=None),
        _env({"type": "text_delta", "delta": "D"}, uid="r2c", parent="r2"),
    ]
    chain = reconstruct_linear_chain(envelopes)
    assert [e.uuid for e in chain] == ["r2", "r2c"]


def test_reconstruct_dangling_parent_terminates_cleanly() -> None:
    # A node whose parent_uuid is not present in the file: the walk stops there.
    envelopes = [
        _env({"type": "text_delta", "delta": "X"}, uid="only", parent="ghost"),
    ]
    chain = reconstruct_linear_chain(envelopes)
    assert [e.uuid for e in chain] == ["only"]


def test_resume_async_is_awaitable_pure_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    path = resolve_session_path("", "sess-async", cwd="/work/proj")
    _write_multi_turn_log(path, [("ping", ["pong"])])

    from magi_agent.cli.session_log import resume_async

    ctx = asyncio.run(resume_async("sess-async", cwd="/work/proj"))
    assert ctx.initial_messages == [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
    ]


# =============================================================================
# PR-B3: §6 hardening (security / parity / coverage)
# =============================================================================
from magi_agent.cli.session_log import (  # noqa: E402
    _StaticTranscriptStore,
    _safe_token,
    prepare_resume_async,
    resume_async,
)


# --- security: path traversal + file perms ------------------------------------
def test_resolve_session_path_neutralizes_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    root = tmp_path / "projects"
    p = resolve_session_path("bot", "../../../etc/passwd", cwd="/work")
    # The resolved path must stay under <root>/projects/<slug>/.
    assert p.resolve().is_relative_to(root.resolve())
    assert ".." not in p.name


def test_safe_token_strips_separators_and_dots() -> None:
    assert "/" not in _safe_token("a/b/c")
    assert ".." not in _safe_token("../../x")
    assert _safe_token("..").strip("-.") == "" or "/" not in _safe_token("..")
    assert _safe_token("ok_session-1.id") == "ok_session-1.id"


def test_session_file_is_owner_only(tmp_path: Path) -> None:
    import stat

    path = tmp_path / "proj" / "s.jsonl"
    log = SessionLog(path)
    log.append(_event(0))
    log.close()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, oct(mode)
    dir_mode = stat.S_IMODE((tmp_path / "proj").stat().st_mode)
    assert dir_mode == 0o700, oct(dir_mode)


# --- parity: _StaticTranscriptStore committed-prefix truncation ---------------
class _KindEntry:
    def __init__(self, kind: str) -> None:
        self.kind = kind


def test_static_store_drops_trailing_uncommitted_turn() -> None:
    entries = [
        _KindEntry("turn_started"),
        _KindEntry("assistant_text"),
        _KindEntry("turn_committed"),
        # trailing in-flight turn (no turn_committed): must be dropped
        _KindEntry("turn_started"),
        _KindEntry("assistant_text"),
    ]
    committed = _StaticTranscriptStore(entries).read_committed()
    assert [e.kind for e in committed] == [
        "turn_started",
        "assistant_text",
        "turn_committed",
    ]


def test_static_store_empty_when_no_committed_turn() -> None:
    entries = [_KindEntry("turn_started"), _KindEntry("assistant_text")]
    assert _StaticTranscriptStore(entries).read_committed() == []


def test_static_store_keeps_trailing_control_event() -> None:
    entries = [_KindEntry("turn_committed"), _KindEntry("control_event")]
    committed = _StaticTranscriptStore(entries).read_committed()
    assert [e.kind for e in committed] == ["turn_committed", "control_event"]


# --- coverage: control_event forward map --------------------------------------
def test_forward_map_control_event(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = [
        _env(
            {"type": "control_event", "eventId": "ev-1", "eventType": "control_resumed", "seq": 3},
            uid="a",
        )
    ]
    entries, dropped, _err = _envelopes_to_transcript_entries(chain, _stub_deps())
    assert dropped == 0
    assert [e.kind for e in entries] == ["ControlEventTranscriptEntry"]
    assert entries[0].kw["eventId"] == "ev-1"
    assert entries[0].kw["eventType"] == "control_resumed"
    assert entries[0].kw["seq"] == 3


def test_forward_map_control_event_missing_id_skipped() -> None:
    chain = [_env({"type": "control_event", "eventType": "x"}, uid="a")]
    entries, dropped, _err = _envelopes_to_transcript_entries(chain, _stub_deps())
    assert entries == [] and dropped == 0  # missing eventId → skipped, not a drop


def test_forward_map_tool_end_iserror_variants() -> None:
    chain = [
        _env({"type": "tool_end", "id": "c1", "status": "needs_approval"}, uid="a"),
        _env({"type": "tool_end", "id": "c2", "status": "error"}, uid="b", parent="a"),
    ]
    entries, _d, _e = _envelopes_to_transcript_entries(chain, _stub_deps())
    assert entries[0].kw["isError"] is False  # needs_approval is not an error
    assert entries[1].kw["isError"] is True


# --- coverage: _payload_user_text tolerant shapes -----------------------------
@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"type": "user_message", "content": "a"}, "a"),
        ({"role": "user", "content": "b"}, "b"),
        ({"message": {"role": "user", "content": "c"}}, "c"),
        ({"type": "user_input", "text": "d"}, "d"),
        ({"role": "user", "prompt": "e"}, "e"),
        ({"type": "text_delta", "delta": "x"}, None),  # not a user message
    ],
)
def test_reconstruct_messages_user_shapes(payload: dict, expected) -> None:
    chain = [_env(payload, uid="u")]
    msgs = reconstruct_messages(chain)
    if expected is None:
        assert all(m["role"] != "user" for m in msgs)
    else:
        assert msgs == [{"role": "user", "content": expected}]


def test_reconstruct_messages_flush_on_turn_start() -> None:
    # Two assistant runs separated ONLY by a turn_start (no turn_end) must NOT
    # merge into one message.
    chain = [
        _env({"type": "text_delta", "delta": "first"}, uid="a"),
        _env({"type": "turn_start"}, uid="b", parent="a"),
        _env({"type": "text_delta", "delta": "second"}, uid="c", parent="b"),
    ]
    assert reconstruct_messages(chain) == [
        {"role": "assistant", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]


# --- coverage: tolerant Envelope on valid-JSON-missing-keys -------------------
def test_load_valid_json_missing_keys_yields_none_fields(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"uuid": "only"}\n')
    [env] = load(path)
    assert env.uuid == "only"
    assert env.parent_uuid is None and env.type is None and env.payload is None


# --- coverage: prepare_resume_async + resume() in running loop ----------------
def test_prepare_resume_async_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    cwd = "/work/proj"
    _write_multi_turn_log(resolve_session_path("botZ", "sz", cwd=cwd), [("hi", ["yo"])])

    ctx = asyncio.run(prepare_resume_async(_Args(continue_=True, bot_id="botZ", cwd=cwd)))
    assert ctx.session_id == "sz"
    assert ctx.initial_messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_prepare_resume_async_no_request() -> None:
    import asyncio

    ctx = asyncio.run(prepare_resume_async(_Args()))
    assert ctx.reason == "no_session_requested"


def test_resume_sync_in_running_loop_degrades_to_pure_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setenv(DEFAULT_SESSION_ROOT_ENV, str(tmp_path))
    cwd = "/work/proj"
    _write_multi_turn_log(resolve_session_path("", "sess-loop", cwd=cwd), [("q", ["a"])])

    async def _inner() -> ResumeContext:
        # Calling the SYNC resume() from inside a running loop must not raise;
        # it degrades to the pure message-list path with a rehydration_skipped reason.
        return resume("sess-loop", cwd=cwd)

    ctx = asyncio.run(_inner())
    assert ctx.initial_messages == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    assert ctx.reason is not None and ctx.reason.startswith("rehydration_skipped")
