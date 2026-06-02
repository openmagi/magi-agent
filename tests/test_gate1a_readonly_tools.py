from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
REQUEST_DIGEST = "sha256:" + "c" * 64


def _scope() -> dict[str, object]:
    return {
        "selectedBotDigest": BOT_DIGEST,
        "selectedOwnerDigest": OWNER_DIGEST,
        "environment": "local",
    }


def _enabled_config(**overrides: object) -> object:
    from magi_agent.gates.gate1a_readonly_tools import (
        Gate1AReadOnlyToolConfig,
    )

    payload: dict[str, object] = {
        "enabled": True,
        "killSwitchEnabled": False,
        "localTestHarnessEnabled": True,
        "selectedBotDigest": BOT_DIGEST,
        "selectedOwnerDigest": OWNER_DIGEST,
        "environment": "local",
        "environmentAllowlist": ("local",),
        "allowedToolNames": (
            "Clock",
            "Calculation",
            "FileRead",
            "Glob",
            "Grep",
            "GitDiff",
            "ArtifactList",
            "ArtifactRead",
            "HealthStatus",
            "TaskList",
            "TaskGet",
            "TaskOutput",
            "CronList",
        ),
        "maxToolCallsPerTurn": 8,
        "maxPerToolOutputBytes": 256,
        "maxAggregateOutputBytes": 512,
    }
    payload.update(overrides)
    return Gate1AReadOnlyToolConfig.model_validate(payload)


def test_gate1a_defaults_off_and_exposes_no_tools(tmp_path: Path) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        Gate1AReadOnlyToolConfig,
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=Gate1AReadOnlyToolConfig(),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    assert bundle.status == "disabled"
    assert bundle.reason == "gate_disabled"
    assert bundle.tools == ()
    assert bundle.exposed_tool_names == ()
    assert bundle.attachment_flags.model_dump(by_alias=True) == {
        "localReadOnlyToolsAttached": False,
        "adkFunctionToolsBuilt": False,
        "routeAttached": False,
        "productionAttached": False,
        "userVisibleOutputAttached": False,
        "writeMutationAllowed": False,
        "bashCommandAllowed": False,
        "memoryWriteAllowed": False,
        "browserSideEffectAllowed": False,
        "telegramDiscordSendAllowed": False,
        "artifactChannelDeliveryAllowed": False,
        "productionTranscriptSseDbWriteAllowed": False,
    }


def test_gate1a_selected_scope_attaches_allowlisted_readonly_function_tools_only(
    tmp_path: Path,
) -> None:
    from google.adk.agents import Agent
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import FunctionTool

    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(),
        scope=_scope(),
        workspace_root=tmp_path,
        now_ms=lambda: 1_779_200_000_000,
    )

    assert bundle.status == "ready"
    assert bundle.reason == "selected_readonly_tools_ready"
    assert bundle.exposed_tool_names == (
        "Clock",
        "Calculation",
        "FileRead",
        "Glob",
        "Grep",
        "GitDiff",
        "ArtifactList",
        "ArtifactRead",
        "HealthStatus",
        "TaskList",
        "TaskGet",
        "TaskOutput",
        "CronList",
    )
    assert [tool.name for tool in bundle.tools] == list(bundle.exposed_tool_names)
    assert all(isinstance(tool, FunctionTool) for tool in bundle.tools)
    agent = Agent(name="gate1a_readonly_test_agent", model="test-model", tools=list(bundle.tools))
    assert len(agent.tools) == len(bundle.tools)
    runner = Runner(
        app_name="gate1a-readonly-test",
        agent=agent,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    assert runner.agent.tools == agent.tools
    assert bundle.attachment_flags.local_read_only_tools_attached is True
    assert bundle.attachment_flags.adk_function_tools_built is True
    assert bundle.attachment_flags.production_attached is False
    assert bundle.attachment_flags.route_attached is False
    assert bundle.attachment_flags.user_visible_output_attached is False
    assert bundle.attachment_flags.write_mutation_allowed is False
    assert bundle.attachment_flags.bash_command_allowed is False
    assert bundle.attachment_flags.memory_write_allowed is False
    assert bundle.attachment_flags.browser_side_effect_allowed is False
    assert bundle.attachment_flags.telegram_discord_send_allowed is False
    assert bundle.attachment_flags.artifact_channel_delivery_allowed is False
    assert bundle.attachment_flags.production_transcript_sse_db_write_allowed is False


def test_gate1a_adk_function_tools_use_concrete_schemas_and_reach_fake_provider_boundary(
    tmp_path: Path,
) -> None:
    from google.adk.agents import Agent
    from google.adk.models import Gemini
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    captured_configs: list[types.GenerateContentConfig] = []

    class _FakeModels:
        async def generate_content(
            self,
            *,
            model: str,
            contents: object,
            config: types.GenerateContentConfig,
        ) -> types.GenerateContentResponse:
            assert model == "gemini-3.5-flash"
            assert contents
            captured_configs.append(config)
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(
                            role="model",
                            parts=[types.Part(text="fake provider boundary reached")],
                        ),
                        finish_reason=types.FinishReason.STOP,
                    )
                ],
                model_version="fake-gemini",
            )

    class _FakeAio:
        models = _FakeModels()

    class _FakeClient:
        vertexai = False
        aio = _FakeAio()

    class _FakeGemini(Gemini):
        @property
        def api_client(self) -> _FakeClient:
            return _FakeClient()

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(routeAttachmentEnabled=True),
        scope=_scope(),
        workspace_root=tmp_path,
        now_ms=lambda: 1_779_200_000_000,
    )

    agent = Agent(
        name="gate1a_readonly_fake_provider_agent",
        model=_FakeGemini(model="gemini-3.5-flash"),
        tools=list(bundle.tools),
        generate_content_config=types.GenerateContentConfig(maxOutputTokens=16),
    )
    runner = Runner(
        app_name="gate1a-readonly-fake-provider",
        agent=agent,
        session_service=InMemorySessionService(),
        auto_create_session=True,
    )

    async def _run() -> list[str]:
        texts: list[str] = []
        async for event in runner.run_async(
            user_id="gate1a-test-user",
            session_id="gate1a-test-session",
            new_message=types.Content(
                role="user",
                parts=[types.Part(text="sanitized test input")],
            ),
        ):
            for part in getattr(getattr(event, "content", None), "parts", []) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    texts.append(text)
        return texts

    texts = asyncio.run(_run())

    assert texts == ["fake provider boundary reached"]
    assert len(captured_configs) == 1
    tools = captured_configs[0].tools or []
    assert len(tools) == 1
    declarations = tools[0].function_declarations or []
    declaration_by_name = {declaration.name: declaration for declaration in declarations}
    assert tuple(declaration_by_name) == bundle.exposed_tool_names
    assert "arguments" not in (
        declaration_by_name["Calculation"].parameters.properties or {}
    )
    assert set(declaration_by_name["Calculation"].parameters.properties or {}) == {
        "expression"
    }
    assert set(declaration_by_name["FileRead"].parameters.properties or {}) == {"path"}
    assert set(declaration_by_name["Grep"].parameters.properties or {}) == {
        "glob",
        "pattern",
    }
    serialized = json.dumps(captured_configs[0].model_dump(by_alias=True), default=str)
    for forbidden in (
        "Authorization",
        "Cookie",
        "token",
        "api_key",
        "sessionKey",
        "/Users/kevin",
        "provider payload",
        "sanitized test input",
    ):
        assert forbidden not in serialized


def test_gate1a_adk_function_tool_dispatches_flat_args_through_toolhost(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(),
        scope=_scope(),
        workspace_root=tmp_path,
    )
    calculation_tool = next(tool for tool in bundle.tools if tool.name == "Calculation")

    result = asyncio.run(
        calculation_tool.run_async(
            args={"expression": "2 + 3 * 4"},
            tool_context=object(),
        )
    )

    assert result["status"] == "ok"
    assert result["outputPreview"] == {"value": 14}
    assert result["receipt"]["allowedToolName"] == "Calculation"
    assert bundle.host.call_count == 1
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "Authorization",
        "Cookie",
        "token",
        "api_key",
        "/Users/kevin",
    ):
        assert forbidden not in serialized


def test_gate1a_route_attachment_flag_is_explicit_and_keeps_production_off(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(
            localTestHarnessEnabled=False,
            routeAttachmentEnabled=True,
        ),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    assert bundle.status == "ready"
    assert bundle.attachment_flags.route_attached is True
    assert bundle.attachment_flags.production_attached is False
    assert bundle.attachment_flags.local_read_only_tools_attached is False
    assert bundle.attachment_flags.user_visible_output_attached is False
    assert bundle.attachment_flags.write_mutation_allowed is False


def test_gate1a_non_selected_scope_exposes_no_tools(tmp_path: Path) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    scope = _scope()
    scope["selectedBotDigest"] = "sha256:" + "d" * 64
    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(),
        scope=scope,
        workspace_root=tmp_path,
    )

    assert bundle.status == "blocked"
    assert bundle.reason == "bot_not_selected"
    assert bundle.tools == ()
    assert bundle.exposed_tool_names == ()
    assert bundle.attachment_flags.local_read_only_tools_attached is False


@pytest.mark.parametrize(
    "tool_name",
    (
        "Bash",
        "TestRun",
        "FileWrite",
        "FileEdit",
        "PatchApply",
        "Delete",
        "CronCreate",
        "CronUpdate",
        "CronDelete",
        "TaskStop",
        "TaskCreate",
        "TaskWait",
        "MemoryWrite",
        "BrowserClick",
        "TelegramSend",
        "DiscordSend",
        "FileDeliver",
        "WorkspaceMutate",
    ),
)
def test_gate1a_forbidden_tools_rejected_before_invocation(
    tmp_path: Path,
    tool_name: str,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(allowedToolNames=("Clock", tool_name)),
        scope=_scope(),
        workspace_root=tmp_path,
    )
    outcome = asyncio.run(
        bundle.host.dispatch(
            tool_name,
            {},
            request_digest=REQUEST_DIGEST,
            tool_call_id=f"call-{tool_name}",
        )
    )

    assert tool_name not in bundle.exposed_tool_names
    assert outcome.status == "blocked"
    assert outcome.reason == "tool_not_allowlisted"
    assert outcome.handler_called is False
    assert outcome.receipt.allowed_tool_name == tool_name
    assert outcome.receipt.status == "blocked"


def test_gate1a_handlers_cap_redact_and_receipt_outputs(tmp_path: Path) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    notes_file = tmp_path / "notes.txt"
    notes_file.write_text(
        "safe prefix Authorization: Bearer live-token /Users/kevin/private\n",
        encoding="utf-8",
    )
    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(maxPerToolOutputBytes=48, maxAggregateOutputBytes=96),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    outcome = asyncio.run(
        bundle.host.dispatch(
            "FileRead",
            {"path": "notes.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="file-read-1",
        )
    )

    dumped = json.dumps(outcome.model_dump(by_alias=True), sort_keys=True)
    assert outcome.status == "ok"
    assert outcome.receipt.status == "ok"
    assert outcome.receipt.allowed_tool_name == "FileRead"
    assert outcome.receipt.output_byte_count <= 48
    assert outcome.receipt.redaction_proof == "redacted"
    assert "live-token" not in dumped
    assert "/Users/kevin" not in dumped
    assert "Authorization" not in dumped
    assert "safe prefix" not in dumped
    assert outcome.output_preview == "[redacted]"


def test_gate1a_glob_and_grep_reject_parent_traversal_and_symlink_escape(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("outside secret match\n", encoding="utf-8")
    inside = tmp_path / "inside.txt"
    inside.write_text("inside match\n", encoding="utf-8")
    link = tmp_path / "linked-secret.txt"
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "nested-secret.txt").write_text("nested outside secret match\n", encoding="utf-8")
    link_dir = tmp_path / "linked-dir"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None
    try:
        link_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        link_dir = None

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(maxToolCallsPerTurn=10, maxAggregateOutputBytes=2048),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    parent_glob = asyncio.run(
        bundle.host.dispatch(
            "Glob",
            {"pattern": "../outside-secret.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="glob-parent",
        )
    )
    safe_glob = asyncio.run(
        bundle.host.dispatch(
            "Glob",
            {"pattern": "*.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="glob-safe",
        )
    )
    parent_grep = asyncio.run(
        bundle.host.dispatch(
            "Grep",
            {"pattern": "outside secret", "glob": "../outside-secret.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="grep-parent",
        )
    )
    safe_grep = asyncio.run(
        bundle.host.dispatch(
            "Grep",
            {"pattern": "inside", "glob": "*.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="grep-safe",
        )
    )
    symlink_dir_glob = asyncio.run(
        bundle.host.dispatch(
            "Glob",
            {"pattern": "linked-dir/*.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="glob-symlink-dir",
        )
    )
    symlink_dir_grep = asyncio.run(
        bundle.host.dispatch(
            "Grep",
            {"pattern": "nested outside", "glob": "linked-dir/*.txt"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="grep-symlink-dir",
        )
    )

    assert parent_glob.status == "ok"
    assert parent_glob.output_preview == {"matches": []}
    assert parent_grep.status == "ok"
    assert parent_grep.output_preview == {"matches": []}
    assert safe_glob.status == "ok"
    safe_matches = safe_glob.output_preview["matches"]
    assert "inside.txt" in safe_matches
    assert "../outside-secret.txt" not in safe_matches
    if link is not None:
        assert "linked-secret.txt" not in safe_matches
    if link_dir is not None:
        assert symlink_dir_glob.output_preview == {"matches": []}
        assert symlink_dir_grep.output_preview == {"matches": []}
    assert safe_grep.status == "ok"
    assert safe_grep.output_preview == {"matches": [{"line": 1, "matched": True}]}
    dumped = json.dumps(safe_grep.model_dump(by_alias=True), sort_keys=True)
    assert "outside-secret.txt" not in dumped
    assert "outside secret" not in dumped


def test_gate1a_blocks_sensitive_workspace_paths_and_skips_them_in_search(
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    (tmp_path / ".env").write_text("TOKEN=live-token\n", encoding="utf-8")
    (tmp_path / ".kube").mkdir()
    (tmp_path / ".kube" / "config").write_text("apiVersion: v1\n", encoding="utf-8")
    (tmp_path / "service-token.txt").write_text("token=live-token\n", encoding="utf-8")
    (tmp_path / "app-config.json").write_text('{"endpoint":"internal"}\n', encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible match\n", encoding="utf-8")
    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(maxToolCallsPerTurn=12, maxAggregateOutputBytes=4096),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    for index, protected_path in enumerate(
        (".env", ".kube/config", "service-token.txt", "app-config.json"),
    ):
        outcome = asyncio.run(
            bundle.host.dispatch(
                "FileRead",
                {"path": protected_path},
                request_digest=REQUEST_DIGEST,
                tool_call_id=f"protected-{index}",
            )
        )
        assert outcome.status == "blocked"
        assert outcome.reason == "path_policy_denied"
        assert outcome.receipt.status == "blocked"
        dumped = json.dumps(outcome.model_dump(by_alias=True), sort_keys=True)
        assert "live-token" not in dumped
        assert "apiVersion" not in dumped

    glob_outcome = asyncio.run(
        bundle.host.dispatch(
            "Glob",
            {"pattern": "**/*"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="glob-sensitive-skip",
        )
    )
    grep_outcome = asyncio.run(
        bundle.host.dispatch(
            "Grep",
            {"pattern": "token|apiVersion|visible", "glob": "**/*"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="grep-sensitive-skip",
        )
    )

    assert glob_outcome.status == "ok"
    assert glob_outcome.output_preview == {"matches": ["visible.txt"]}
    assert grep_outcome.status == "ok"
    assert grep_outcome.output_preview == {
        "matches": [{"line": 1, "matched": True}],
    }
    dumped = json.dumps(
        [glob_outcome.model_dump(by_alias=True), grep_outcome.model_dump(by_alias=True)],
        sort_keys=True,
    )
    assert ".env" not in dumped
    assert ".kube" not in dumped
    assert "service-token" not in dumped
    assert "app-config" not in dumped
    assert "live-token" not in dumped
    assert "apiVersion" not in dumped


def test_gate1a_glob_implementation_does_not_call_pathlib_glob_with_raw_pattern() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "gates"
        / "gate1a_readonly_tools.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert ".glob(pattern)" not in source
    assert ".glob(glob_pattern)" not in source
    assert "os.walk(" in source
    assert "followlinks=False" in source


def test_gate1a_duplicate_same_digest_does_not_call_handler_twice(tmp_path: Path) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    first = asyncio.run(
        bundle.host.dispatch(
            "Calculation",
            {"expression": "2 + 3 * 4"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="calc-1",
        )
    )
    duplicate = asyncio.run(
        bundle.host.dispatch(
            "Calculation",
            {"expression": "2 + 3 * 4"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="calc-1",
        )
    )

    assert first.status == "ok"
    assert duplicate.status == "duplicate"
    assert duplicate.reason == "duplicate_tool_call"
    assert duplicate.handler_called is False
    assert first.receipt.tool_call_digest == duplicate.receipt.tool_call_digest
    assert bundle.host.call_count == 1


def test_gate1a_counter_conflicts_and_limits_fail_closed(tmp_path: Path) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        build_gate1a_readonly_tool_bundle,
    )

    bundle = build_gate1a_readonly_tool_bundle(
        config=_enabled_config(maxToolCallsPerTurn=1, maxAggregateOutputBytes=24),
        scope=_scope(),
        workspace_root=tmp_path,
    )

    first = asyncio.run(
        bundle.host.dispatch(
            "Clock",
            {},
            request_digest=REQUEST_DIGEST,
            tool_call_id="clock-1",
        )
    )
    blocked = asyncio.run(
        bundle.host.dispatch(
            "Calculation",
            {"expression": "1+1"},
            request_digest=REQUEST_DIGEST,
            tool_call_id="calc-2",
        )
    )
    conflict = asyncio.run(
        bundle.host.dispatch(
            "Clock",
            {"changed": True},
            request_digest=REQUEST_DIGEST,
            tool_call_id="clock-1",
        )
    )

    assert first.status == "ok"
    assert blocked.status == "blocked"
    assert blocked.reason == "max_tool_calls_exhausted"
    assert blocked.handler_called is False
    assert conflict.status == "blocked"
    assert conflict.reason == "tool_call_digest_conflict"
    assert conflict.handler_called is False


def test_gate1a_import_boundary_has_no_route_deploy_network_provider_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "gates"
        / "gate1a_readonly_tools.py"
    )
    source = module_path.read_text(encoding="utf-8")

    for forbidden in (
        "magi_agent.transport.chat",
        "magi_agent.transport.sse",
        "magi_agent.memory",
        "magi_agent.browser",
        "magi_agent.channels",
        "magi_agent.web_acquisition",
        "requests",
        "httpx",
        "kubectl",
        "supabase",
    ):
        assert forbidden not in source
