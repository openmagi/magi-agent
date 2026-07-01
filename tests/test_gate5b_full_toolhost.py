import json
import os
from collections.abc import Mapping, Sequence

import pytest

from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _sha256(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="test", build_sha="sha-test"),
        )
    )


@pytest.mark.asyncio
async def test_selected_scope_exposes_full_workspace_tools_and_receipts(tmp_path):
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    assert bundle.status == "ready"
    assert [tool.name for tool in bundle.tools] == list(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    assert set(bundle.exposed_tool_names) >= {"FileRead", "FileWrite", "FileEdit", "PatchApply", "Bash"}

    write_outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "notes/hello.txt", "content": "hello from selected toolhost\n"},
        request_digest=_sha256("request-1"),
        tool_call_id="call-write-1",
    )

    assert write_outcome.status == "ok"
    assert (tmp_path / "notes/hello.txt").read_text(encoding="utf-8") == (
        "hello from selected toolhost\n"
    )
    receipt = write_outcome.coding_mutation_receipt
    assert receipt is not None
    projection = receipt.public_projection()
    assert projection["toolName"] == "FileWrite"
    assert projection["status"] == "success"
    assert projection["productionWorkspaceMutationAllowed"] is False
    assert projection["workspaceDigest"].startswith("sha256:")

    bash_outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "printf ok"},
        request_digest=_sha256("request-1"),
        tool_call_id="call-bash-1",
    )

    assert bash_outcome.status == "ok"
    assert bash_outcome.coding_mutation_receipt is not None


@pytest.mark.asyncio
async def test_full_toolhost_dispatch_emits_live_public_tool_progress_events(tmp_path):
    public_events: list[dict[str, object]] = []
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        public_event_sink=lambda event: public_events.append(dict(event)),
    )

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "notes/live.txt", "content": "live event content\n"},
        request_digest=_sha256("request-live-events"),
        tool_call_id="call-live-events",
    )

    assert outcome.status == "ok"
    assert [event["type"] for event in public_events] == [
        "tool_start",
        "tool_progress",
        "tool_end",
    ]
    assert public_events[0]["name"] == "FileWrite"
    assert public_events[1]["status"] == "in_progress"
    assert public_events[2]["status"] == "ok"
    assert public_events[0]["id"] == public_events[1]["id"] == public_events[2]["id"]
    serialized = json.dumps(public_events)
    assert "live event content" not in serialized
    assert "receipt:sha256:" in serialized


@pytest.mark.asyncio
async def test_full_toolhost_dispatch_public_tool_start_includes_safe_input_preview(tmp_path):
    public_events: list[dict[str, object]] = []
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
        public_event_sink=lambda event: public_events.append(dict(event)),
    )

    await bundle.host.dispatch(
        "WebSearch",
        {"query": "openmagi gate5b streaming"},
        request_digest=_sha256("request-web-preview"),
        tool_call_id="call-web-preview",
    )

    start_event = public_events[0]
    assert start_event["type"] == "tool_start"
    assert start_event["name"] == "WebSearch"
    assert start_event["input_preview"] == '{"query":"openmagi gate5b streaming"}'


@pytest.mark.asyncio
async def test_selected_scope_exposes_first_party_registry_tools_with_gate5b_receipts(tmp_path):
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )
    (tmp_path / "secrets.yaml").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "ROOT.md").write_text("SECRET=2", encoding="utf-8")

    assert bundle.status == "ready"
    attached_names = [tool.name for tool in bundle.tools]
    assert {"WebSearch", "Browser", "DocumentWrite", "SkillLoader"}.issubset(attached_names)

    web = await bundle.host.dispatch(
        "WebSearch",
        {"query": "Open Magi"},
        request_digest=_sha256("request-registry-1"),
        tool_call_id="call-web-1",
    )
    document = await bundle.host.dispatch(
        "DocumentWrite",
        {"path": "docs/report.md", "content": "hello"},
        request_digest=_sha256("request-registry-1"),
        tool_call_id="call-document-1",
    )

    assert web.status == "ok"
    assert web.receipt.tool_name == "WebSearch"
    assert document.status == "ok"
    assert document.receipt.tool_name == "DocumentWrite"
    assert (tmp_path / "docs/report.md").read_text(encoding="utf-8") == "hello"
    assert bundle.host.counter.receipt_count == 2


@pytest.mark.asyncio
async def test_selected_registry_spawn_agent_disabled_child_runner_is_blocked(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    outcome = await bundle.host.dispatch(
        "SpawnAgent",
        {"prompt": "assign a helper"},
        request_digest=_sha256("request-spawn-disabled"),
        tool_call_id="call-spawn-disabled",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "live_child_runner_disabled"


@pytest.mark.asyncio
async def test_selected_registry_spawn_agent_emits_live_child_events(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    class _FakeLiveChildRunner:
        openmagi_live_provider = True

        def __init__(self, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> Mapping[str, object]:
            return {
                "childExecutionId": "child-exec-gate5b-live",
                "status": "completed",
                "summary": "Delegated child completed.",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _FakeLiveChildRunner)
    public_events: list[dict[str, object]] = []
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
        public_event_sink=lambda event: public_events.append(dict(event)),
    )

    outcome = await bundle.host.dispatch(
        "SpawnAgent",
        {"prompt": "assign a helper"},
        request_digest=_sha256("request-spawn-live-events"),
        tool_call_id="call-spawn-live-events",
    )

    assert outcome.status == "ok"
    preview = outcome.output_preview
    assert isinstance(preview, dict)
    assert preview["status"] == "ok"
    assert preview["errorCode"] is None
    metadata = preview["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["toolName"] == "SpawnAgent"
    assert metadata["handler"] == "first_party_native_local"
    assert str(metadata["outputDigest"]).startswith("sha256:")
    output = preview["output"]
    llm_output = preview["llmOutput"]
    assert isinstance(output, dict)
    # `output` keeps the full bookkeeping envelope for the evidence layer; the
    # LLM-facing `llmOutput` is the answer-forward projection (no bookkeeping).
    assert isinstance(llm_output, dict)
    assert output != llm_output
    assert llm_output["status"] == "ok"
    assert llm_output["result"] == "Delegated child completed."
    assert "liveChildRunnerAttached" not in llm_output
    assert output["status"] == "ok"
    assert output["liveChildRunnerAttached"] is True
    assert output["summary"] == "Delegated child completed."
    event_types = [event["type"] for event in public_events]
    assert event_types == [
        "tool_start",
        "tool_progress",
        "child_started",
        "child_progress",
        "child_completed",
        "tool_end",
    ]
    assert event_types.index("child_started") < event_types.index("tool_end")
    child_events = [
        event for event in public_events if str(event.get("type", "")).startswith("child_")
    ]
    assert {event["taskId"] for event in child_events} == {"call-spawn-live-events"}
    assert all(
        str(event["childReceiptRef"]).startswith("receipt:sha256:")
        for event in child_events
    )
    # Privacy contract: PROMPT body never leaks. The child SUMMARY preview
    # (the same string the parent LLM already consumes via the tool result) IS
    # surfaced on ``child_completed`` so the UI chip can hint at what the
    # agent came back with — see ``child_completed_event`` builder.
    assert "assign a helper" not in json.dumps(child_events, sort_keys=True)
    completed = next(
        event for event in child_events if event.get("type") == "child_completed"
    )
    assert completed.get("summary") == "Delegated child completed."


@pytest.mark.asyncio
async def test_selected_registry_spawn_agent_preserves_live_attached_blocked_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    import magi_agent.runtime.child_runner_live as _live_mod

    class _BlockedLiveChildRunner:
        openmagi_live_provider = True

        def __init__(self, **kwargs: object) -> None:
            pass

        async def run_child(self, request: object) -> Mapping[str, object]:
            return {
                "childExecutionId": "child-exec-gate5b-blocked",
                "status": "blocked",
                "summary": "child_provider_key_missing",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    monkeypatch.setattr(_live_mod, "RealLocalChildRunner", _BlockedLiveChildRunner)
    public_events: list[dict[str, object]] = []
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
        public_event_sink=lambda event: public_events.append(dict(event)),
    )

    outcome = await bundle.host.dispatch(
        "SpawnAgent",
        {"prompt": "assign a helper"},
        request_digest=_sha256("request-spawn-live-blocked"),
        tool_call_id="call-spawn-live-blocked",
    )

    assert outcome.status == "ok"
    preview = outcome.output_preview
    assert isinstance(preview, dict)
    assert preview["status"] == "blocked"
    assert preview["errorCode"] == "child_provider_key_missing"
    output = preview["output"]
    assert isinstance(output, dict)
    assert output["liveChildRunnerAttached"] is True
    assert output["childRunnerAvailability"] == "live_attached"
    assert output["childExecutionFailed"] is True
    assert output["childFailureReason"] == "child_provider_key_missing"
    metadata = preview["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["liveChildRunnerAttached"] is True
    assert metadata["childRunnerAvailability"] == "live_attached"
    assert metadata["childExecutionFailed"] is True
    assert [event["type"] for event in public_events] == [
        "tool_start",
        "tool_progress",
        "child_started",
        "child_progress",
        "child_cancelled",
        "tool_end",
    ]
    assert public_events[-1]["status"] == "ok"


def test_selected_full_toolhost_adk_declarations_are_google_schema_compatible(tmp_path):
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    assert bundle.status == "ready"
    assert "AgentMemoryRemember" in bundle.exposed_tool_names
    for tool in bundle.tools:
        declaration = tool._get_declaration()
        assert declaration is not None
        payload = declaration.model_dump(by_alias=True, exclude_none=True, mode="json")
        assert not _contains_key(payload, "additional_properties")
        assert not _contains_key(payload, "additionalProperties")
        assert not _contains_key(payload, "anyOf")
        assert not _contains_key(payload, "any_of")


def test_selected_full_toolhost_spawn_agent_declares_delegation_schema(tmp_path):
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    spawn = next(tool for tool in bundle.tools if tool.name == "SpawnAgent")
    declaration = spawn._get_declaration()
    assert declaration is not None
    payload = declaration.model_dump(by_alias=True, exclude_none=True, mode="json")
    properties = payload["parameters"]["properties"]

    assert {"prompt", "task", "persona", "provider", "model", "budgetMs"}.issubset(
        set(properties)
    )
    assert "query" not in properties
    assert "path" not in properties
    assert "content" not in properties
    assert "delegate" in str(payload["description"]).lower()
    assert "subtask" in str(payload["description"]).lower()


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_key(child, key) for child in value)
    return False


@pytest.mark.asyncio
async def test_registry_tools_inherit_secret_and_sealed_path_policy(tmp_path):
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    secret_write = await bundle.host.dispatch(
        "DocumentWrite",
        {"path": ".env.local", "content": "secret"},
        request_digest=_sha256("request-policy-1"),
        tool_call_id="call-secret-write",
    )
    sealed_write = await bundle.host.dispatch(
        "DocumentWrite",
        {"path": "AGENTS.md", "content": "sealed"},
        request_digest=_sha256("request-policy-1"),
        tool_call_id="call-sealed-write",
    )
    secret_read = await bundle.host.dispatch(
        "BatchRead",
        {"paths": [".env.local", "AGENTS.md"]},
        request_digest=_sha256("request-policy-1"),
        tool_call_id="call-secret-read",
    )
    code_search = await bundle.host.dispatch(
        "CodeSymbolSearch",
        {"query": "SECRET"},
        request_digest=_sha256("request-policy-1"),
        tool_call_id="call-code-search",
    )

    assert secret_write.status == "blocked"
    assert secret_write.reason == "secret_path_denied"
    assert sealed_write.status == "blocked"
    assert sealed_write.reason == "sealed_file_write_blocked"
    assert secret_read.status == "ok"
    assert secret_read.output_preview is not None
    assert "secret_path_denied" in str(secret_read.output_preview)
    assert "sealed_file_read_blocked" in str(secret_read.output_preview)
    assert code_search.status == "ok"
    assert isinstance(code_search.output_preview, dict)
    assert code_search.output_preview["output"]["matches"] == []
    assert not (tmp_path / ".env.local").exists()
    assert not (tmp_path / "AGENTS.md").exists()


@pytest.mark.asyncio
async def test_registry_tools_block_hidden_workspace_mutation(tmp_path):
    runtime = _runtime()
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    hidden_write = await bundle.host.dispatch(
        "DocumentWrite",
        {"path": ".git/hooks/pre-commit", "content": "echo blocked"},
        request_digest=_sha256("request-hidden-policy"),
        tool_call_id="call-hidden-write",
    )

    assert hidden_write.status == "blocked"
    assert hidden_write.reason == "protected_git_path"
    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.asyncio
async def test_full_toolhost_blocks_path_escape_and_non_selected_scope(tmp_path):
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    non_selected = build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256("other-bot"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )
    assert non_selected.status == "blocked"
    assert non_selected.tools == ()

    selected = build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    blocked = await selected.host.dispatch(
        "FileWrite",
        {"path": "../escape.txt", "content": "nope"},
        request_digest=_sha256("request-2"),
        tool_call_id="call-write-escape",
    )

    assert blocked.status == "blocked"
    assert blocked.reason == "path_escapes_workspace"
    assert not (tmp_path.parent / "escape.txt").exists()

    duplicate = await selected.host.dispatch(
        "FileWrite",
        {"path": "../escape.txt", "content": "nope"},
        request_digest=_sha256("request-2"),
        tool_call_id="call-write-escape",
    )

    assert duplicate.status == "duplicate"
    assert duplicate.reason == "duplicate_tool_call"
    assert duplicate.receipt == blocked.receipt


@pytest.mark.asyncio
async def test_full_toolhost_conflicting_replay_preserves_original_receipt(tmp_path):
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    first = await bundle.host.dispatch(
        "FileWrite",
        {"path": "notes/one.txt", "content": "first"},
        request_digest=_sha256("request-conflict"),
        tool_call_id="call-conflict",
    )
    conflict = await bundle.host.dispatch(
        "FileWrite",
        {"path": "notes/two.txt", "content": "second"},
        request_digest=_sha256("request-conflict"),
        tool_call_id="call-conflict",
    )

    assert first.status == "ok"
    assert conflict.status == "blocked"
    assert conflict.reason == "tool_call_digest_conflict"
    assert conflict.receipt == first.receipt
    assert bundle.host.counter.receipt_count == 1
    assert bundle.host.counter.receipts == (first.receipt,)


@pytest.mark.asyncio
async def test_legacy_bash_uses_selected_full_toolhost_hard_safety(tmp_path):
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )

    blocked = await bundle.host.dispatch(
        "Bash",
        {"command": "cat ~/.ssh/id_rsa"},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-secret-path",
    )

    assert blocked.status == "blocked"
    assert blocked.reason == "shell_path_expansion_denied"
    assert blocked.handler_called is False

    (tmp_path / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    inline_interpreter = await bundle.host.dispatch(
        "Bash",
        {"command": "python -c \"print(open('.env.local').read())\""},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-inline-interpreter",
    )

    assert inline_interpreter.status == "blocked"
    assert inline_interpreter.reason == "interpreter_inline_code_denied"
    assert inline_interpreter.handler_called is False

    pipeline_read = await bundle.host.dispatch(
        "Bash",
        {"command": "cat .env.local | head -n 1"},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-pipeline-secret",
    )
    attached_inline_flag = await bundle.host.dispatch(
        "Bash",
        {"command": "python3 -c'print(open(\".env.local\").read())'"},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-attached-inline-flag",
    )
    env_wrapper = await bundle.host.dispatch(
        "Bash",
        {"command": "/usr/bin/env python3 -c \"print(open('.env.local').read())\""},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-env-wrapper-inline",
    )
    env_split_wrapper = await bundle.host.dispatch(
        "Bash",
        {"command": "/usr/bin/env -S python3 -c \"print(open('.env.local').read())\""},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-env-split-wrapper-inline",
    )
    env_attached_split_wrapper = await bundle.host.dispatch(
        "Bash",
        {"command": "/usr/bin/env -Spython3 -c \"print(open('.env.local').read())\""},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-env-attached-split-wrapper-inline",
    )
    newline_compound = await bundle.host.dispatch(
        "Bash",
        {"command": "printf ok\ncat .env.local"},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-newline-compound",
    )
    ampersand_compound = await bundle.host.dispatch(
        "Bash",
        {"command": "printf ok & cat .env.local"},
        request_digest=_sha256("request-bash-policy"),
        tool_call_id="call-bash-ampersand-compound",
    )

    assert pipeline_read.status == "blocked"
    assert pipeline_read.reason == "complex_shell_requires_approval"
    assert pipeline_read.handler_called is False
    assert attached_inline_flag.status == "blocked"
    assert attached_inline_flag.reason == "interpreter_inline_code_denied"
    assert attached_inline_flag.handler_called is False
    assert env_wrapper.status == "blocked"
    assert env_wrapper.reason == "interpreter_inline_code_denied"
    assert env_wrapper.handler_called is False
    assert env_split_wrapper.status == "blocked"
    assert env_split_wrapper.reason == "interpreter_inline_code_denied"
    assert env_split_wrapper.handler_called is False
    assert env_attached_split_wrapper.status == "blocked"
    assert env_attached_split_wrapper.reason == "interpreter_inline_code_denied"
    assert env_attached_split_wrapper.handler_called is False
    assert newline_compound.status == "blocked"
    assert newline_compound.reason == "complex_shell_requires_approval"
    assert newline_compound.handler_called is False
    assert ampersand_compound.status == "blocked"
    assert ampersand_compound.reason == "complex_shell_requires_approval"
    assert ampersand_compound.handler_called is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
@pytest.mark.asyncio
async def test_code_symbol_search_skips_workspace_symlinks(tmp_path):
    runtime = _runtime()
    outside = tmp_path.parent / "outside-secret-symbol.py"
    outside.write_text("OUTSIDE_SYMBOL = 'do not read through symlink'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    os.symlink(outside, tmp_path / "src" / "outside.py")
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    code_search = await bundle.host.dispatch(
        "CodeSymbolSearch",
        {"query": "OUTSIDE_SYMBOL"},
        request_digest=_sha256("request-symlink-policy"),
        tool_call_id="call-code-search-symlink",
    )

    assert code_search.status == "ok"
    assert isinstance(code_search.output_preview, dict)
    assert code_search.output_preview["output"]["matches"] == []


@pytest.mark.asyncio
async def test_code_symbol_search_skips_oversized_text_files(tmp_path):
    runtime = _runtime()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "large.py").write_text(
        "OVERSIZED_SYMBOL = 1\n" + ("x" * 1_100_000),
        encoding="utf-8",
    )
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    code_search = await bundle.host.dispatch(
        "CodeSymbolSearch",
        {"query": "OVERSIZED_SYMBOL"},
        request_digest=_sha256("request-large-file-policy"),
        tool_call_id="call-code-search-large",
    )

    assert code_search.status == "ok"
    assert isinstance(code_search.output_preview, dict)
    assert code_search.output_preview["output"]["matches"] == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
@pytest.mark.asyncio
async def test_workspace_writes_block_symlink_to_protected_git_path(tmp_path):
    runtime = _runtime()
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    os.symlink(tmp_path / ".git", tmp_path / "gitlink")
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        tool_registry=runtime.tool_registry,
    )

    native_write = await bundle.host.dispatch(
        "DocumentWrite",
        {"path": "gitlink/hooks/pre-commit", "content": "echo blocked"},
        request_digest=_sha256("request-git-symlink-policy"),
        tool_call_id="call-native-git-symlink",
    )
    legacy_write = await bundle.host.dispatch(
        "FileWrite",
        {"path": "gitlink/hooks/pre-commit", "content": "echo blocked"},
        request_digest=_sha256("request-git-symlink-policy"),
        tool_call_id="call-legacy-git-symlink",
    )

    assert native_write.status == "blocked"
    assert native_write.reason == "protected_git_path"
    assert legacy_write.status == "blocked"
    assert legacy_write.reason == "protected_git_path"
    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.asyncio
async def test_dispatch_unwraps_arguments_envelope(tmp_path):
    """The empty legacy tool inputSchema ({type:object, additionalProperties})
    leads some ADK providers to nest the call args under a top-level
    ``arguments`` key (``{"arguments": {"path": ..., "content": ...}}``) instead
    of passing them flat. Dispatch must unwrap that envelope so the write does
    not silently no-op (path resolves to empty -> nothing written)."""
    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 8,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )
    assert bundle.status == "ready"

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"arguments": {"path": "wrapped.txt", "content": "unwrapped-ok\n"}},
        request_digest=_sha256("request-wrap"),
        tool_call_id="call-wrap-1",
    )

    assert outcome.status == "ok"
    assert (tmp_path / "wrapped.txt").read_text(encoding="utf-8") == "unwrapped-ok\n"
