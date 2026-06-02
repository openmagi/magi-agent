from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.tools.context import ToolContext


def test_minimal_tool_context_remains_backwards_compatible() -> None:
    context = ToolContext(
        bot_id="bot-1",
        turn_id="turn-1",
        workspace_root="/tmp/workspace",
    )

    assert context.bot_id == "bot-1"
    assert context.turn_id == "turn-1"
    assert context.workspace_root == "/tmp/workspace"
    assert context.memory_mode == MemoryMode.NORMAL
    assert context.spawn_depth == 0


def test_prd_tool_context_aliases_parse_and_dump() -> None:
    context = ToolContext(
        botId="bot-1",
        userId="user-1",
        sessionKey="agent:main:app:channel-1",
        turnId="turn-1",
        workspaceRoot="/tmp/workspace",
        memoryMode="read_only",
        channel="app",
        locale="ko",
        currentUserMessage="hello",
        traceId="trace-1",
        toolUseId="tool-use-1",
        abortSignal={"aborted": False},
        deadlineMs=30_000,
        permissionScope="workspace:read",
        filesRead=["README.md"],
        sourceLedger=[{"url": "https://example.com/source"}],
        executionContract={"mode": "act"},
        staging={"path": "/tmp/staging"},
        commitHandle={"id": "commit-1"},
        spawnDepth=2,
        spawnWorkspace="/tmp/workspace/subtask",
        pluginId="plugin-1",
        secretScope="bot",
        secretBroker={"kind": "broker"},
        adkToolContext={"opaque": "tool-context"},
        adkContext={"opaque": "context"},
    )

    assert context.session_key == "agent:main:app:channel-1"
    assert context.memory_mode == MemoryMode.READ_ONLY
    assert context.current_user_message == "hello"
    assert context.trace_id == "trace-1"
    assert context.tool_use_id == "tool-use-1"
    assert context.abort_signal == {"aborted": False}
    assert context.permission_scope == "workspace:read"
    assert context.files_read == ("README.md",)
    assert context.source_ledger == ({"url": "https://example.com/source"},)
    assert context.execution_contract == {"mode": "act"}
    assert context.staging == {"path": "/tmp/staging"}
    assert context.commit_handle == {"id": "commit-1"}
    assert context.spawn_depth == 2
    assert context.spawn_workspace == "/tmp/workspace/subtask"
    assert context.plugin_id == "plugin-1"
    assert context.secret_scope == "bot"
    assert context.secret_broker == {"kind": "broker"}
    assert context.adk_tool_context == {"opaque": "tool-context"}
    assert context.adk_context == {"opaque": "context"}

    dumped = context.model_dump(by_alias=True)
    assert dumped["botId"] == "bot-1"
    assert dumped["sessionKey"] == "agent:main:app:channel-1"
    assert dumped["memoryMode"] == "read_only"
    assert dumped["currentUserMessage"] == "hello"
    assert dumped["traceId"] == "trace-1"
    assert dumped["toolUseId"] == "tool-use-1"
    assert dumped["abortSignal"] == {"aborted": False}
    assert dumped["deadlineMs"] == 30_000
    assert dumped["permissionScope"] == "workspace:read"
    assert dumped["filesRead"] == ["README.md"]
    assert dumped["sourceLedger"] == [{"url": "https://example.com/source"}]
    assert dumped["executionContract"] == {"mode": "act"}
    assert dumped["staging"] == {"path": "/tmp/staging"}
    assert dumped["commitHandle"] == {"id": "commit-1"}
    assert dumped["spawnDepth"] == 2
    assert dumped["spawnWorkspace"] == "/tmp/workspace/subtask"
    assert dumped["pluginId"] == "plugin-1"
    assert dumped["secretScope"] == "bot"
    assert dumped["secretBroker"] == {"kind": "broker"}
    assert dumped["adkToolContext"] == {"opaque": "tool-context"}
    assert dumped["adkContext"] == {"opaque": "context"}


def test_current_user_message_accepts_structured_payloads() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "summarize this"},
            {
                "type": "attachment",
                "file": {"name": "brief.pdf", "mimeType": "application/pdf"},
            },
        ],
        "context": {"channel": "app", "locale": "ko"},
    }

    context = ToolContext(
        bot_id="bot-1",
        currentUserMessage=message,
    )

    assert context.current_user_message == message
    assert context.model_dump(by_alias=True)["currentUserMessage"] == message


def test_files_read_and_source_ledger_store_list_inputs_as_tuples() -> None:
    context = ToolContext(
        bot_id="bot-1",
        filesRead=["README.md"],
        sourceLedger=[{"url": "https://example.com/source"}],
    )

    assert context.files_read == ("README.md",)
    assert context.source_ledger == ({"url": "https://example.com/source"},)

    with pytest.raises(AttributeError):
        context.files_read.append("WORKING.md")

    with pytest.raises(AttributeError):
        context.source_ledger.append({"url": "https://example.com/other"})


def test_files_read_rejects_mutable_non_path_entries() -> None:
    with pytest.raises(ValidationError, match="filesRead"):
        ToolContext(
            bot_id="bot-1",
            filesRead=[{"path": "README.md"}],
        )


def test_source_ledger_recursively_freezes_nested_payloads() -> None:
    context = ToolContext(
        bot_id="bot-1",
        sourceLedger=[
            {
                "url": "https://example.com/source",
                "metadata": {
                    "quotes": ["alpha"],
                    "scores": [{"value": 1}],
                },
            },
        ],
    )

    entry = context.source_ledger[0]
    assert entry == {
        "url": "https://example.com/source",
        "metadata": {
            "quotes": ["alpha"],
            "scores": [{"value": 1}],
        },
    }

    with pytest.raises(TypeError):
        entry["url"] = "https://example.com/changed"

    with pytest.raises(AttributeError):
        entry["metadata"]["quotes"].append("beta")

    with pytest.raises(TypeError):
        entry["metadata"]["scores"][0]["value"] = 2

    assert context.model_dump(by_alias=True)["sourceLedger"] == [
        {
            "url": "https://example.com/source",
            "metadata": {
                "quotes": ["alpha"],
                "scores": [{"value": 1}],
            },
        },
    ]


def test_callbacks_and_object_fields_are_storable_without_invocation() -> None:
    calls: list[str] = []

    def callback(*_args: object, **_kwargs: object) -> None:
        calls.append("called")

    adk_bridge = object()
    context = ToolContext(
        bot_id="bot-1",
        turn_id="turn-1",
        workspace_root="/tmp/workspace",
        emitProgress=callback,
        emitAgentEvent=callback,
        emitControlEvent=callback,
        askUser=callback,
        staging=object(),
        commitHandle=object(),
        secretBroker=object(),
        adkToolContext=adk_bridge,
    )

    assert context.emit_progress is callback
    assert context.emit_agent_event is callback
    assert context.emit_control_event is callback
    assert context.ask_user is callback
    assert context.adk_tool_context is adk_bridge
    assert calls == []


def test_negative_spawn_depth_is_rejected() -> None:
    with pytest.raises(ValidationError, match="spawnDepth"):
        ToolContext(
            bot_id="bot-1",
            turn_id="turn-1",
            workspace_root="/tmp/workspace",
            spawnDepth=-1,
        )
