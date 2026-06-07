from __future__ import annotations

from types import SimpleNamespace

from magi_agent.observability.projector import project, project_public_event


def _ctx(**kw):
    base = dict(session_id="s1", run_id="r1", tool_name=None, error=None, summary=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_before_tool_use_maps_to_tool_start():
    ev = project("beforeToolUse", _ctx(tool_name="read"))
    assert ev is not None
    assert ev.kind == "tool_start"
    assert ev.status == "running"
    assert ev.tool_name == "read"
    assert ev.session_id == "s1"


def test_after_tool_use_maps_to_tool_end_ok():
    ev = project("afterToolUse", _ctx(tool_name="edit"))
    assert ev.kind == "tool_end"
    assert ev.status == "ok"


def test_on_error_maps_to_error():
    ev = project("onError", _ctx(error="boom"))
    assert ev.kind == "error"
    assert ev.status == "error"
    assert ev.summary == "boom"


def test_unmapped_point_returns_none():
    assert project("beforeSystemPrompt", _ctx()) is None


def test_run_id_falls_back_to_turn_id():
    ev = project("beforeToolUse", SimpleNamespace(session_id="s1", turn_id="t1"))
    assert ev is not None
    assert ev.run_id == "t1"
    assert ev.session_id == "s1"


# --- project_public_event tests ---

def test_public_tool_start_maps_kind_and_tool_name():
    payload = {"type": "tool_start", "toolName": "bash", "toolUseId": "u1"}
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert ev.kind == "tool_start"
    assert ev.status is None
    assert ev.tool_name == "bash"
    assert ev.session_id == "s1"
    assert ev.run_id == "t1"


def test_public_tool_start_falls_back_to_name_key():
    payload = {"type": "tool_start", "name": "read_file"}
    ev = project_public_event(payload, session_id="s1", turn_id="t2")
    assert ev is not None
    assert ev.tool_name == "read_file"


def test_public_tool_end_with_status():
    payload = {"type": "tool_end", "toolName": "bash", "status": "ok"}
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert ev.kind == "tool_end"
    assert ev.status == "ok"
    assert ev.tool_name == "bash"


def test_public_event_without_type_returns_none():
    payload = {"toolName": "bash", "status": "ok"}
    assert project_public_event(payload, session_id="s1", turn_id="t1") is None


def test_public_non_dict_returns_none():
    assert project_public_event("not-a-dict", session_id="s1", turn_id="t1") is None  # type: ignore[arg-type]
    assert project_public_event(None, session_id="s1", turn_id="t1") is None  # type: ignore[arg-type]
    assert project_public_event(42, session_id="s1", turn_id="t1") is None  # type: ignore[arg-type]


def test_public_event_payload_is_bounded():
    big = "x" * 5000
    payload = {"type": "tool_end", "toolName": "bash", "status": "ok",
               "outputPreview": big, "nested": {"a": 1}, "n": 7}
    ev = project_public_event(payload, session_id="s1", turn_id="t1")
    assert ev is not None
    assert len(ev.payload["outputPreview"]) == 512
    assert ev.payload["n"] == 7
    assert "nested" not in ev.payload  # nested dict dropped
    assert "type" not in ev.payload
