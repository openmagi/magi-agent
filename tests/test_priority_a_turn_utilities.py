from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from magi_agent.recipes.retry_repair_policies import default_recipe_retry_repair_rules
from magi_agent.runtime.turn_utilities import (
    RetryController,
    classify_retry_taxonomy,
    detect_polling_iteration,
    extract_discovered_tool_names,
    extract_user_visible_text,
    normalize_route_value,
    normalize_user_visible_route_meta_tags,
)


def _recipe_retry_controller() -> RetryController:
    return RetryController(max_attempts=3, repair_rules=default_recipe_retry_repair_rules())


def _task_get_result(status: str) -> dict[str, Any]:
    return {
        "content": f'{{"taskId":"spawn_abc","status":"{status}","startedAt":123}}',
        "isError": False,
    }


def test_retry_controller_resamples_before_commit_blocks_and_records_metadata() -> None:
    controller = RetryController(max_attempts=3)

    decision = controller.next(
        {
            "kind": "before_commit_blocked",
            "reason": "unsupported claim",
            "attempt": 1,
        }
    )

    assert decision.action == "resample"
    assert decision.tool_policy == "normal"
    assert "unsupported claim" in decision.hidden_user_message
    assert decision.taxonomy == "retry"
    assert controller.exhausted is False
    assert controller.last_error is not None
    assert controller.last_error.kind == "before_commit_blocked"
    assert controller.last_error.reason == "unsupported claim"
    assert controller.last_error.attempt == 1


def test_retry_controller_handles_structured_output_and_research_proof_prompts() -> None:
    controller = _recipe_retry_controller()

    structured = controller.next(
        {
            "kind": "structured_output_invalid",
            "reason": "bad json",
            "attempt": 1,
        }
    )
    assert structured.action == "resample"
    assert structured.taxonomy == "retry"

    research = controller.next(
        {
            "kind": "before_commit_blocked",
            "reason": (
                "[RETRY:CLAIM_CITATION]\n"
                "Available inspected sources:\n"
                "- [src_1] web_fetch - https://example.com\n"
                "Missing citation examples:\n"
                "1. Example claim"
            ),
            "attempt": 1,
        }
    )
    assert research.action == "resample"
    assert research.tool_policy == "text_only"
    assert "Do not call tools" in research.hidden_user_message
    assert "[src_1]" in research.hidden_user_message


def test_retry_controller_specializes_goal_interactive_and_edit_reflections() -> None:
    controller = _recipe_retry_controller()

    goal = controller.next(
        {
            "kind": "before_commit_blocked",
            "reason": "[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]\nExecute next.",
            "attempt": 1,
        }
    )
    assert goal.action == "resample"
    assert "must use the necessary tool" in goal.hidden_user_message
    assert "SpawnAgent" in goal.hidden_user_message

    interactive = controller.next(
        {
            "kind": "before_commit_blocked",
            "reason": "[RETRY:INTERACTIVE_TOOL_REQUIRED]\nBrowser evidence missing.",
            "attempt": 1,
        }
    )
    assert interactive.action == "resample"
    assert "Browser or SocialBrowser" in interactive.hidden_user_message

    not_unique = controller.next(
        {
            "kind": "edit_apply_failed",
            "reason": "old_string appears more than once",
            "attempt": 1,
            "errorCode": "not_unique",
        }
    )
    assert not_unique.action == "resample"
    assert "surrounding context" in not_unique.hidden_user_message
    assert controller.last_error is not None
    assert controller.last_error.error_code == "not_unique"

    lazy_output = controller.next(
        {
            "kind": "edit_apply_failed",
            "reason": "placeholder comment detected",
            "attempt": 1,
            "errorCode": "lazy_output",
        }
    )
    assert lazy_output.action == "resample"
    assert "complete replacement" in lazy_output.hidden_user_message


def test_retry_controller_aborts_on_max_attempts_and_reset_clears_exhaustion() -> None:
    controller = RetryController(max_attempts=3)

    exhausted = controller.next(
        {
            "kind": "edit_apply_failed",
            "reason": "old_string not found",
            "attempt": 3,
        }
    )
    assert exhausted.action == "abort"
    assert exhausted.reason == "old_string not found"
    assert exhausted.taxonomy == "fail_closed"
    assert controller.exhausted is True
    assert controller.last_error is not None
    assert controller.last_error.attempt == 3

    controller.reset()

    assert controller.exhausted is False
    assert controller.last_error is None


def test_retry_controller_core_default_does_not_embed_recipe_specific_repairs() -> None:
    controller = RetryController(max_attempts=3)

    decision = controller.next(
        {
            "kind": "edit_apply_failed",
            "reason": "old_string was not found",
            "attempt": 1,
            "errorCode": "not_found",
        }
    )

    assert decision.action == "resample"
    assert "FileEdit" not in decision.hidden_user_message
    assert "runtime verifier" in decision.hidden_user_message


@pytest.mark.parametrize(
    ("code_or_kind", "expected"),
    (
        ("before_commit_blocked", "retry"),
        ("structured_output_invalid", "retry"),
        ("edit_apply_failed", "retry"),
        ("provider_error", "fail_open"),
        ("timeout", "fail_open"),
        ("context_overflow", "fail_open"),
        ("redaction_failure", "fail_closed"),
        ("budget_exceeded", "fail_closed"),
        ("model_routing_invalid", "fail_closed"),
        ("user_interrupt", "fail_closed"),
    ),
)
def test_retry_taxonomy_matches_retry_fail_open_fail_closed_contract(
    code_or_kind: str,
    expected: str,
) -> None:
    assert classify_retry_taxonomy(code_or_kind) == expected


def test_route_meta_normalization_matches_typescript_visible_text_cases() -> None:
    assert normalize_user_visible_route_meta_tags(
        "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]"
        "\nI will draft the memo from the source material."
    ) == (
        "[META: intent=execution, domain=document writing, complexity=complex, route=subagent]"
        "\nI will draft the memo from the source material."
    )

    assert normalize_user_visible_route_meta_tags(
        "[META: intent=execution, domain=document writing, complexity=complex, route=subagent]"
        "\n자료를 확인한 뒤 메모 초안을 작성하겠습니다."
    ) == (
        "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]"
        "\n자료를 확인한 뒤 메모 초안을 작성하겠습니다."
    )

    repeated = (
        "[META: intent=실행, domain=문서작성, complexity=complex, route=subagent]"
        "지금 바로 시작합니다."
        "[META: intent=실행, domain=문서작성, complexity=simple, route=direct]"
        "[META: intent=실행, domain=문서작성, complexity=simple, route=direct]"
        "백그라운드 에이전트 기다리느라 시간 낭비했습니다."
    )
    assert normalize_user_visible_route_meta_tags(repeated) == (
        "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]"
        "지금 바로 시작합니다.백그라운드 에이전트 기다리느라 시간 낭비했습니다."
    )

    prose = "설명: [META: this is part of the actual reply]"
    assert normalize_user_visible_route_meta_tags(prose) == prose


def test_route_meta_route_alias_normalization_rejects_malformed_japanese_subagent() -> None:
    assert normalize_route_value("サフエーシェント") is None


def test_visible_text_extraction_only_keeps_text_deltas_and_response_clear_boundary() -> None:
    events = [
        {"type": "turn_start", "turnId": "turn-1", "declaredRoute": "direct"},
        {"type": "text_delta", "delta": "Draft that should be cleared. "},
        {"type": "tool_start", "name": "Bash", "input_preview": "hidden command"},
        {"type": "thinking_delta", "delta": "private thought"},
        {"type": "response_clear", "reason": "retry"},
        {
            "type": "text_delta",
            "delta": "[META: intent=실행, domain=문서작성, complexity=복잡, route=서브에이전트]",
            "routeMetadata": {"unsafe": "ignore"},
        },
        {"type": "text_delta", "delta": "\nI will write the final memo."},
        {"type": "tool_end", "output_preview": "raw internal tool result"},
        {"type": "runtime_trace", "detail": "not assistant text"},
        {"type": "turn_end", "turnId": "turn-1", "status": "committed"},
    ]

    assert extract_user_visible_text(events) == (
        "[META: intent=execution, domain=document writing, complexity=complex, route=subagent]"
        "\nI will write the final memo."
    )


def test_discovered_tools_extracts_ordered_deduped_tool_references_from_user_results() -> None:
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_result",
                    "content": [{"type": "tool_reference", "tool_name": "Ignored"}],
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "raw": [{"type": "tool_reference", "tool_name": "UnsafeRaw"}],
                    "content": [
                        {"type": "tool_reference", "tool_name": "Browser"},
                        {"type": "tool_reference", "tool_name": "CronCreate"},
                    ],
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_2",
                    "content": [
                        {"type": "tool_reference", "tool_name": "Browser"},
                        {"type": "tool_reference", "tool_name": 42},
                    ],
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_3",
                    "content": "some text result",
                }
            ],
        },
    ]

    assert extract_discovered_tool_names(messages) == ("Browser", "CronCreate")


def test_polling_detector_matches_taskget_status_semantics_without_side_effects() -> None:
    assert detect_polling_iteration([], []).is_polling is False
    assert detect_polling_iteration(
        ["Bash", "FileRead"],
        [{"content": "ok", "isError": False}, {"content": "data", "isError": False}],
    ).is_polling is False
    assert detect_polling_iteration(
        ["TaskGet", "Bash"],
        [_task_get_result("running"), {"content": "output", "isError": False}],
    ).is_polling is False

    running = detect_polling_iteration(
        ["TaskGet", "TaskGet"],
        [_task_get_result("running"), _task_get_result("pending")],
    )
    assert running.is_polling is True
    assert running.all_still_running is True

    completed = detect_polling_iteration(
        ["TaskGet", "TaskGet"],
        [_task_get_result("running"), _task_get_result("completed")],
    )
    assert completed.is_polling is True
    assert completed.all_still_running is False

    errored = detect_polling_iteration(
        ["TaskGet"],
        [{"content": "not json", "isError": False}],
    )
    assert errored.is_polling is True
    assert errored.all_still_running is False


def test_turn_utilities_import_boundary_stays_pure_local_metadata_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.runtime.turn_utilities")
assert hasattr(module, "RetryController")

forbidden_exact = (
    "google.adk",
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "subprocess",
    "fastapi",
    "starlette.routing",
)
forbidden_prefixes = (
    "magi_agent.adk_bridge",
    "magi_agent.transport",
    "magi_agent.channels",
    "magi_agent.tools",
    "magi_agent.deploy",
    "magi_agent.provisioning",
    "magi_agent.k8s",
    "magi_agent.database",
    "magi_agent.supabase",
    "magi_agent.api",
    "magi_agent.proxy",
    "magi_agent.dashboard",
    "magi_agent.scheduler",
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
    raise AssertionError(f"turn utilities import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

    root = Path(__file__).parents[1]
    module_path = root / "magi_agent" / "runtime" / "turn_utilities.py"
    source = module_path.read_text(encoding="utf-8")
    forbidden_source_imports = (
        "google",
        "google.adk",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "fastapi",
        "starlette",
        "magi_agent.adk_bridge",
        "magi_agent.transport",
        "magi_agent.channels",
        "magi_agent.tools",
        "magi_agent.deploy",
        "magi_agent.provisioning",
        "magi_agent.k8s",
        "magi_agent.database",
        "magi_agent.supabase",
        "magi_agent.api",
        "magi_agent.proxy",
        "magi_agent.dashboard",
        "magi_agent.scheduler",
    )
    for forbidden in forbidden_source_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "ToolHost" not in source
    assert "ToolDispatcher" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
