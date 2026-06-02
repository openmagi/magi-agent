from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _boundary() -> Any:
    try:
        return importlib.import_module("magi_agent.runtime.commit_boundary")
    except ModuleNotFoundError as exc:
        pytest.fail(f"commit boundary module is missing: {exc}")


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, tuple):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _intents(plan: Any) -> list[dict[str, Any]]:
    return _dump(plan)["intents"]


def _operations(plan: Any) -> list[str]:
    return [intent["operation"] for intent in _intents(plan)]


def _targets(plan: Any, operation: str) -> list[str]:
    return [
        intent["target"]
        for intent in _intents(plan)
        if intent["operation"] == operation
    ]


def _assert_default_off_intents(plan: Any) -> None:
    dumped = _dump(plan)
    assert dumped["enabled"] is False
    assert dumped["defaultOff"] is True
    assert "default-off" in dumped["disabledReason"]
    assert dumped["intents"], "plans should expose intent records"
    for intent in dumped["intents"]:
        assert intent["executed"] is False
        assert intent["enabled"] is False
        assert intent["defaultOff"] is True
        assert "default-off" in intent["disabledReason"]


def test_final_assistant_text_collects_only_text_normalizes_meta_and_trims_prefix() -> None:
    boundary = _boundary()

    final_text = boundary.collect_final_assistant_text(
        [
            {"type": "thinking", "thinking": "private chain of thought"},
            {
                "type": "text",
                "text": (
                    "  [META: intent=execution, domain=document writing, "
                    "complexity=complex, route=subagent]\n자료를 확인했습니다."
                ),
            },
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
            {"type": "text", "text": "[META: route=direct] 완료했습니다."},
        ]
    )

    assert final_text == (
        "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]"
        "\n자료를 확인했습니다.완료했습니다."
    )
    assert "private chain of thought" not in final_text


def test_commit_plan_records_default_off_commit_and_hook_intents() -> None:
    boundary = _boundary()

    plan = boundary.build_commit_plan(
        blocks=[
            {"type": "text", "text": "  Hello "},
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": "world."},
            {"type": "tool_use", "name": "FileRead", "input": {"path": "readme.md"}},
            {"type": "tool_use", "name": "FileWrite", "input": {"path": "a.ts"}},
        ],
        turn_id="turn-1",
        user_message="do work",
        usage={"inputTokens": 1, "outputTokens": 2, "costUsd": 0},
        started_at=10,
        ended_at=20,
    )
    dumped = _dump(plan)

    assert dumped["status"] == "committed"
    assert dumped["finalText"] == "Hello world."
    assert _operations(plan) == [
        "beforeCommit",
        "assistant_text",
        "turn_committed",
        "stop_reason",
        "turn_end",
        "legacy_finish",
        "afterCommit",
        "afterTurnEnd",
        "onTaskCheckpoint",
    ]
    assert _targets(plan, "assistant_text") == ["transcript"]
    assert _targets(plan, "turn_end") == ["sse"]
    assert _targets(plan, "legacy_finish") == ["sse"]
    before_commit = next(
        intent for intent in _intents(plan) if intent["operation"] == "beforeCommit"
    )
    assert before_commit["target"] == "hook"
    assert before_commit["payload"] == {
        "assistantText": "Hello world.",
        "toolCallCount": 2,
        "toolReadHappened": True,
        "userMessage": "do work",
        "retryCount": 0,
        "toolNames": ["FileRead", "FileWrite"],
        "filesChanged": ["a.ts"],
    }
    checkpoint = next(
        intent for intent in _intents(plan) if intent["operation"] == "onTaskCheckpoint"
    )
    assert checkpoint["target"] == "hook"
    assert checkpoint["payload"]["toolNames"] == ["FileRead", "FileWrite"]
    assert checkpoint["payload"]["filesChanged"] == ["a.ts"]
    _assert_default_off_intents(plan)


def test_commit_plan_checkpoint_omits_unknown_timestamps() -> None:
    boundary = _boundary()

    plan = boundary.build_commit_plan(
        blocks=[{"type": "text", "text": "done"}],
        turn_id="turn-no-times",
        user_message="do work",
    )

    checkpoint = next(
        intent for intent in _intents(plan) if intent["operation"] == "onTaskCheckpoint"
    )
    assert "startedAt" not in checkpoint["payload"]
    assert "endedAt" not in checkpoint["payload"]


def test_commit_plan_skips_empty_assistant_text_but_keeps_terminal_intents() -> None:
    boundary = _boundary()

    plan = boundary.build_commit_plan(
        blocks=[{"type": "thinking", "thinking": "hidden"}],
        turn_id="turn-empty",
        user_message="think",
    )

    assert _dump(plan)["finalText"] == ""
    assert "assistant_text" not in _operations(plan)
    assert "beforeCommit" in _operations(plan)
    assert "turn_committed" in _operations(plan)
    assert "turn_end" in _operations(plan)
    assert "legacy_finish" in _operations(plan)
    _assert_default_off_intents(plan)


def test_before_commit_block_plan_has_no_commit_writes_and_classifies_actions() -> None:
    boundary = _boundary()

    plan = boundary.build_before_commit_block_plan(
        reason=(
            "[RETRY:GOAL_PROGRESS_EXECUTE_NEXT] The draft promised work "
            "without calling SpawnAgent."
        ),
        blocks=[
            {"type": "text", "text": "x"},
            {"type": "tool_use", "name": "Grep", "input": {"path": "src"}},
            {"type": "tool_use", "name": "FileEdit", "input": {"path": "src/app.ts"}},
        ],
        turn_id="turn-1",
        user_message="do work",
        retry_count=2,
    )
    dumped = _dump(plan)

    assert dumped["status"] == "blocked"
    assert dumped["retryable"] is True
    assert dumped["retryKind"] == "before_commit_blocked"
    assert dumped["reasonCode"] == "GOAL_PROGRESS_EXECUTE_NEXT"
    assert dumped["requiredAction"] == (
        "Call the required tool or runtime action before answering."
    )
    assert dumped["finalText"] == "x"
    before_commit = next(
        intent for intent in _intents(plan) if intent["operation"] == "beforeCommit"
    )
    assert before_commit["target"] == "hook"
    assert before_commit["payload"] == {
        "assistantText": "x",
        "toolCallCount": 2,
        "toolReadHappened": True,
        "userMessage": "do work",
        "retryCount": 2,
        "toolNames": ["Grep", "FileEdit"],
        "filesChanged": ["src/app.ts"],
    }
    assert "assistant_text" not in _operations(plan)
    assert "turn_committed" not in _operations(plan)
    assert _operations(plan).index("beforeCommit") < _operations(plan).index("runtime_trace")
    assert _targets(plan, "runtime_trace") == ["sse", "control"]
    trace_payloads = [
        intent["payload"]
        for intent in _intents(plan)
        if intent["operation"] == "runtime_trace"
    ]
    assert all(payload["retryable"] is True for payload in trace_payloads)
    assert all(payload["reasonCode"] == "GOAL_PROGRESS_EXECUTE_NEXT" for payload in trace_payloads)
    _assert_default_off_intents(plan)


@pytest.mark.parametrize(
    ("reason", "expected"),
    (
        ("[RULE:SEALED_FILES] Do not mutate sealed files.", False),
        ("[RULE:MEMORY_MUTATION_TOOL_REQUIRED] Use memory tool.", False),
        ("[RULE:CLAIM_CITATION_REQUIRED] Cite sources.", False),
        ("[RULE:CLAIM_CITATION_GATE_ERROR] verifier failed.", False),
        ("hook:beforeCommit threw: boom", False),
        ("hook:beforeCommit timed out after 3000ms", False),
        ("[RULE:GOAL_PROGRESS_EXECUTE_NEXT] keep working", True),
        ("[RULE:INTERACTIVE_TOOL_REQUIRED] browser evidence missing", True),
    ),
)
def test_is_before_commit_block_retryable_mirrors_typescript_rules(
    reason: str,
    expected: bool,
) -> None:
    boundary = _boundary()

    assert boundary.is_before_commit_block_retryable(reason) is expected


@pytest.mark.parametrize(
    ("reason", "expected_code", "expected_action"),
    (
        (
            "[RULE:INTERACTIVE_TOOL_REQUIRED] Browser required.",
            "INTERACTIVE_TOOL_REQUIRED",
            "Use Browser or SocialBrowser before answering.",
        ),
        (
            "[RULE:CLAIM_CITATION_REQUIRED] Cite inspected sources.",
            "CLAIM_CITATION_REQUIRED",
            "Cite inspected sources or remove unsupported claims.",
        ),
        (
            "[RULE:ARTIFACT_DELIVERY_FILE_REQUIRED] Missing file.",
            "ARTIFACT_DELIVERY_FILE_REQUIRED",
            "Deliver the requested artifact before claiming completion.",
        ),
    ),
)
def test_before_commit_block_plan_extracts_reason_code_required_action_categories(
    reason: str,
    expected_code: str,
    expected_action: str,
) -> None:
    boundary = _boundary()

    plan = boundary.build_before_commit_block_plan(
        reason=reason,
        blocks=[],
        turn_id="turn-2",
    )
    dumped = _dump(plan)

    assert dumped["reasonCode"] == expected_code
    assert dumped["requiredAction"] == expected_action


def test_structured_output_block_assessment_maps_retry_kind_and_stop_reason_without_commits() -> None:
    boundary = _boundary()

    invalid = boundary.build_structured_output_block_plan(
        {
            "status": "invalid",
            "schemaName": "ScoreSchema",
            "reason": "score must be a number; token=secret-token",
        },
        blocks=[{"type": "text", "text": '{"summary":"ok","score":"bad"}'}],
        turn_id="turn-structured",
    )
    exhausted = boundary.build_structured_output_block_plan(
        {"status": "retry_exhausted", "reason": "schema retries exhausted"},
        blocks=[{"type": "text", "text": '{"summary":"ok","score":"bad"}'}],
        turn_id="turn-structured",
    )

    invalid_dump = _dump(invalid)
    exhausted_dump = _dump(exhausted)
    assert invalid_dump["status"] == "blocked"
    assert invalid_dump["retryable"] is True
    assert invalid_dump["retryKind"] == "structured_output_invalid"
    assert invalid_dump["stopReason"] is None
    assert invalid_dump["reason"] == "score must be a number; token=[redacted]"
    assert exhausted_dump["retryable"] is False
    assert exhausted_dump["retryKind"] == "structured_output_invalid"
    assert exhausted_dump["stopReason"] == "structured_output_retry_exhausted"
    assert "assistant_text" not in _operations(invalid)
    assert "turn_committed" not in _operations(invalid)
    assert "assistant_text" not in _operations(exhausted)
    assert "turn_committed" not in _operations(exhausted)
    assert _operations(invalid) == [
        "structured_output",
        "structured_output",
        "runtime_trace",
        "runtime_trace",
    ]
    structured_payloads = [
        intent["payload"]
        for intent in _intents(invalid)
        if intent["operation"] == "structured_output"
    ]
    assert _targets(invalid, "structured_output") == ["sse", "control"]
    assert structured_payloads == [
        {
            "type": "structured_output",
            "turnId": "turn-structured",
            "status": "invalid",
            "schemaName": "ScoreSchema",
            "reason": "score must be a number; token=[redacted]",
        },
        {
            "type": "structured_output",
            "turnId": "turn-structured",
            "status": "invalid",
            "schemaName": "ScoreSchema",
            "reason": "score must be a number; token=[redacted]",
        },
    ]
    _assert_default_off_intents(invalid)
    _assert_default_off_intents(exhausted)


def test_abort_plan_records_default_off_abort_trace_finish_hooks_and_pending_ask_rejection() -> None:
    boundary = _boundary()

    plan = boundary.build_abort_plan(
        turn_id="turn-1",
        user_message="stop",
        reason="user-cancelled token=secret-token",
        cached_assistant_text="previous committed text",
        stop_reason="aborted",
    )
    dumped = _dump(plan)

    assert dumped["status"] == "aborted"
    assert dumped["reason"] == "user-cancelled token=[redacted]"
    assert _operations(plan) == [
        "reject_pending_asks",
        "turn_aborted",
        "stop_reason",
        "runtime_trace",
        "runtime_trace",
        "turn_end",
        "legacy_finish",
        "onAbort",
        "afterTurnEnd",
    ]
    assert _targets(plan, "reject_pending_asks") == ["local_runtime"]
    assert _targets(plan, "turn_aborted") == ["transcript"]
    assert _targets(plan, "stop_reason") == ["control"]
    assert _targets(plan, "runtime_trace") == ["sse", "control"]
    turn_end = next(intent for intent in _intents(plan) if intent["operation"] == "turn_end")
    assert turn_end["payload"] == {
        "type": "turn_end",
        "turnId": "turn-1",
        "status": "aborted",
        "stopReason": "aborted",
        "reason": "user-cancelled token=[redacted]",
    }
    after_turn_end = next(
        intent for intent in _intents(plan) if intent["operation"] == "afterTurnEnd"
    )
    assert after_turn_end["payload"]["assistantText"] == "previous committed text"
    assert after_turn_end["payload"]["status"] == "aborted"
    assert after_turn_end["payload"]["reason"] == "user-cancelled token=[redacted]"
    _assert_default_off_intents(plan)


def test_abort_plan_does_not_expose_partial_emitted_draft_by_default() -> None:
    boundary = _boundary()

    plan = boundary.build_abort_plan(
        turn_id="turn-abort",
        user_message="stop",
        reason="user stopped while partial draft existed",
    )

    after_turn_end = next(
        intent for intent in _intents(plan) if intent["operation"] == "afterTurnEnd"
    )
    assert after_turn_end["payload"]["assistantText"] == ""


def test_collect_files_changed_matches_typescript_workspace_mutation_scan() -> None:
    boundary = _boundary()

    assert boundary.collect_files_changed(
        [
            {"type": "text", "text": "x"},
            {"type": "tool_use", "name": "FileWrite", "input": {"path": "a.ts"}},
            {"type": "tool_use", "name": "FileEdit", "input": {"path": "b.ts"}},
            {"type": "tool_use", "name": "Grep", "input": {"path": "ignored.ts"}},
            {"type": "tool_use", "name": "FileWrite", "input": {"path": "a.ts"}},
            {"type": "tool_use", "name": "FileEdit", "input": {"path": 42}},
            {
                "type": "tool_use",
                "name": "PatchApply",
                "input": {
                    "patch": "\n".join(
                        [
                            "--- a/src/patched.ts",
                            "+++ b/src/patched.ts",
                            "@@ -1 +1 @@",
                            "-old",
                            "+new",
                        ]
                    )
                },
            },
            {
                "type": "tool_use",
                "name": "PatchApply",
                "input": {
                    "dry_run": True,
                    "patch": "--- a/src/dry-run.ts\n+++ b/src/dry-run.ts",
                },
            },
            {
                "type": "tool_use",
                "name": "SpawnWorktreeApply",
                "input": {"action": "preview", "spawnDir": ".spawn/preview"},
            },
            {
                "type": "tool_use",
                "name": "SpawnWorktreeApply",
                "input": {"action": "apply", "spawnDir": ".spawn/apply"},
            },
            {
                "type": "tool_use",
                "name": "SpawnWorktreeApply",
                "input": {"action": "cherry_pick"},
            },
        ]
    ) == ["a.ts", "b.ts", "src/patched.ts", ".spawn/apply", "SpawnWorktreeApply"]


def test_collect_files_changed_emits_only_safe_workspace_relative_display_paths() -> None:
    boundary = _boundary()

    changed = boundary.collect_files_changed(
        [
            {
                "type": "tool_use",
                "name": "FileWrite",
                "input": {"path": "/data/bots/bot-123/workspace/src/safe.ts"},
            },
            {
                "type": "tool_use",
                "name": "FileEdit",
                "input": {"path": "/workspace/app/page.tsx"},
            },
            {"type": "tool_use", "name": "FileWrite", "input": {"path": "../private"}},
            {
                "type": "tool_use",
                "name": "FileWrite",
                "input": {"path": "notes/123456789:AASecretTelegramBotTokenName.md"},
            },
            {
                "type": "tool_use",
                "name": "PatchApply",
                "input": {
                    "patch": "\n".join(
                        [
                            "--- a/../private",
                            "+++ b/src/from-patch.ts",
                            "--- /workspace/raw.ts",
                            "+++ /data/bots/bot/workspace/src/from-prod.ts",
                        ]
                    )
                },
            },
        ]
    )

    assert changed == [
        "src/safe.ts",
        "app/page.tsx",
        "src/from-patch.ts",
        "raw.ts",
        "src/from-prod.ts",
    ]
    assert not any(path.startswith("/") or ".." in path for path in changed)
    assert not any("/data/bots" in path or "/workspace" in path for path in changed)
    assert not any("AASecretTelegramBotTokenName" in path for path in changed)


def test_public_trace_detail_redacts_secrets_collapses_whitespace_and_caps_length() -> None:
    boundary = _boundary()

    detail = boundary.public_trace_detail(
        f"""
        Authorization: Bearer abc.def_123
        Authorization: Basic dXNlcjpwYXNz
        Cookie: session=abc123; csrf=def456
        Set-Cookie: refresh=ghi789; HttpOnly
        ghp_deadbeefTOKEN
        sk-live_abc123
        123456789:AASecretTelegramBotToken{""}SecretValue
        api_key: very-secret-value
        token="another-secret"
        secret='third-secret'
        password=hunter2
        hiddenReasoning: private chain of thought
        privateToolPreview="raw tool output"
        privateKey: -----BEGIN PRIVATE KEY----- abc -----END PRIVATE KEY-----
        /data/bots/bot-123/workspace/secret.txt
        /workspace/private/secret.txt
        /Users/kevin/.ssh/id_rsa
        """
        + ("x" * 700)
    )

    assert "\n" not in detail
    assert "  " not in detail
    for leaked in (
        "abc.def_123",
        "dXNlcjpwYXNz",
        "session=abc123",
        "csrf=def456",
        "refresh=ghi789",
        "ghp_deadbeefTOKEN",
        "sk-live_abc123",
        "123456789:AASecretTelegramBotTokenSecretValue",
        "very-secret-value",
        "another-secret",
        "third-secret",
        "hunter2",
        "private chain of thought",
        "raw tool output",
        "BEGIN PRIVATE KEY",
        "/data/bots",
        "/workspace",
        "/Users/kevin",
    ):
        assert leaked not in detail
    assert "Bearer [redacted]" in detail
    assert "Basic [redacted]" in detail
    assert "[redacted]" in detail
    assert len(detail) == 500
    assert detail.endswith("...")


def test_public_trace_detail_redacts_telegram_api_urls_and_env_style_secrets() -> None:
    boundary = _boundary()

    detail = boundary.public_trace_detail(
        "telegram=https://api.telegram.org/bot123456789:AASecretTelegramBotToken/sendMessage "
        "STRIPE_SECRET_KEY=sk_live_should_not_leak "
        'SUPABASE_SERVICE_ROLE_KEY="service-role-secret"'
    )

    assert "123456789:AASecretTelegramBotToken" not in detail
    assert "bot123456789:AASecretTelegramBotToken" not in detail
    assert "sk_live_should_not_leak" not in detail
    assert "service-role-secret" not in detail
    assert "STRIPE_SECRET_KEY=[redacted]" in detail
    assert "SUPABASE_SERVICE_ROLE_KEY=" in detail


def test_public_trace_detail_redacts_private_path_fields_and_production_internal_urls() -> None:
    boundary = _boundary()

    detail = boundary.public_trace_detail(
        'path=/data/bots/bot-123/workspace/secret.txt '
        '"path":"/workspace/private/notes.md" '
        "callback=https://openmagi.ai/internal/turns/turn-1"
    )

    assert "/data/bots" not in detail
    assert "/workspace" not in detail
    assert "secret.txt" not in detail
    assert "private/notes.md" not in detail
    assert "https://openmagi.ai/internal" not in detail
    assert "[redacted-path]" in detail


def test_abort_and_trace_projection_reason_fields_are_sanitized() -> None:
    boundary = _boundary()

    plan = boundary.build_abort_plan(
        turn_id="turn-redact",
        user_message="stop",
        reason="failed with Cookie: sid=secret and /data/bots/bot/workspace/private.txt",
    )
    serialized = str(_dump(plan))

    assert "sid=secret" not in serialized
    assert "/data/bots" not in serialized
    assert "Cookie: [redacted]" in serialized


def test_intent_and_plan_reject_runtime_enabled_or_executed_construction() -> None:
    boundary = _boundary()

    valid_intent = boundary.CommitIntent(target="sse", operation="turn_end")
    assert _dump(valid_intent)["executed"] is False
    assert _dump(valid_intent)["enabled"] is False
    assert _dump(valid_intent)["defaultOff"] is True

    with pytest.raises(ValueError):
        boundary.CommitIntent(target="sse", operation="turn_end", executed=True)
    with pytest.raises(ValueError):
        boundary.CommitIntent(target="sse", operation="turn_end", enabled=True)
    with pytest.raises(ValueError):
        boundary.CommitIntent(target="sse", operation="turn_end", defaultOff=False)
    with pytest.raises(ValueError):
        replace(valid_intent, enabled=True)

    valid_plan = boundary.CommitBoundaryPlan(status="committed", intents=(valid_intent,))
    assert _dump(valid_plan)["executed"] is False
    assert _dump(valid_plan)["enabled"] is False
    assert _dump(valid_plan)["defaultOff"] is True

    with pytest.raises(ValueError):
        boundary.CommitBoundaryPlan(
            status="committed",
            intents=(valid_intent,),
            executed=True,
        )
    with pytest.raises(ValueError):
        boundary.CommitBoundaryPlan(
            status="committed",
            intents=(valid_intent,),
            enabled=True,
        )
    with pytest.raises(ValueError):
        boundary.CommitBoundaryPlan(
            status="committed",
            intents=(valid_intent,),
            defaultOff=False,
        )
    with pytest.raises(ValueError):
        replace(valid_plan, defaultOff=False)


def test_commit_boundary_import_is_local_planner_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.runtime.commit_boundary")
assert hasattr(module, "build_commit_plan")

forbidden_exact = (
    "google.adk",
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "subprocess",
    "kubernetes",
    "supabase",
    "psycopg",
    "asyncpg",
    "magi_agent.tools.dispatcher",
    "magi_agent.transport.sse",
)
forbidden_prefixes = (
    "google.adk",
    "magi_agent.tools",
    "magi_agent.transport",
    "magi_agent.channels",
    "magi_agent.adk_bridge",
    "magi_agent.memory",
    "magi_agent.workspace",
)
loaded = [
    loaded_name
    for loaded_name in sys.modules
    if loaded_name in forbidden_exact
    or any(loaded_name.startswith(f"{name}.") for name in forbidden_exact)
    or any(
        loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"commit boundary import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_commit_boundary_source_forbids_runtime_side_effect_imports() -> None:
    root = Path(__file__).parents[1]
    module_path = root / "magi_agent" / "runtime" / "commit_boundary.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "asyncio",
        "fastapi",
        "starlette",
        "kubernetes",
        "supabase",
        "psycopg",
        "asyncpg",
        "magi_agent.tools",
        "magi_agent.transport",
        "magi_agent.channels",
        "magi_agent.adk_bridge",
        "magi_agent.memory",
        "magi_agent.workspace",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "FunctionTool(" not in source
    assert "LongRunningFunctionTool(" not in source
    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "add_api_route" not in source
    assert "kubectl" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
