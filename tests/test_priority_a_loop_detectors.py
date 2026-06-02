from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


def _module():
    return importlib.import_module("openmagi_core_agent.runtime.loop_detectors")


def test_repetition_detector_does_not_trigger_on_normal_text() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1)

    result = detector.feed(
        "안녕하세요. 오늘 회의에서 논의된 내용을 정리해드리겠습니다. "
        "첫 번째로 예산 계획에 대해 이야기했고, 두 번째로 인력 배치를 논의했습니다. "
        "세 번째로는 마케팅 전략에 대한 브레인스토밍을 진행했습니다."
    )

    assert result.detected is False


def test_repetition_detector_detects_repeated_korean_sentence_pattern() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1)
    repeated = (
        "사장님, KB에 직접 파일 업로드 기능이 없어요. "
        "document-reader 스킬로 업로드하는 것 같습니다. 확인하겠습니다."
    )

    result = detector.feed((repeated + " ") * 4)

    assert result.detected is True
    assert result.count is not None
    assert result.count >= 3


def test_repetition_detector_detects_repeated_substring_pattern() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1)
    chunk = (
        "This is a long enough repeated pattern that should be detected "
        "by the sliding window algorithm. "
    )

    result = detector.feed(chunk * 4)

    assert result.detected is True
    assert result.pattern is not None
    assert len(result.pattern) <= 80


def test_repetition_detector_diagnostic_matches_ts_candidate_order() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1)
    chunk = (
        "This is a long enough repeated pattern that should be detected "
        "by the sliding window algorithm. "
    )

    result = detector.feed(chunk * 4)

    assert result.detected is True
    assert result.count == 3
    assert (
        result.pattern
        == "a long enough repeated pattern that should be detected by the sliding window alg"
    )


def test_repetition_detector_ignores_short_repeated_patterns_under_min_length() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1, min_pattern_len=40)

    result = detector.feed("네. " * 10)

    assert result.detected is False


def test_repetition_detector_respects_check_interval_but_force_check_sees_buffer() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=500)
    chunk = (
        "A fairly long repeated sentence that should only be detected "
        "when the interval allows a check. "
    )

    interval_result = detector.feed(chunk * 3)
    forced_result = detector.check()

    assert interval_result.detected is False
    assert forced_result.detected is True
    assert detector.get_text() == chunk * 3


def test_repetition_detector_detects_incremental_repetition_and_reset_clears_state() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1)
    sentence = "확인하겠습니다. document-reader 스킬에서 업로드 기능을 찾아보겠습니다. "

    result = detector.feed(sentence)
    assert result.detected is False
    for _ in range(4):
        result = detector.feed(sentence)

    assert result.detected is True

    detector.reset()
    clean = detector.feed("Fresh normal text that should not trigger anything at all.")
    assert clean.detected is False
    assert detector.get_text() == "Fresh normal text that should not trigger anything at all."


def test_repetition_detector_respects_custom_repeat_threshold() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1, repeat_threshold=5)
    chunk = "A fairly long pattern that we want to repeat many times to test threshold config. "

    result = detector.feed(chunk * 4)
    assert result.detected is False

    result = detector.feed(chunk)
    assert result.detected is True


def test_repetition_detector_does_not_false_positive_on_similar_different_sentences() -> None:
    module = _module()
    detector = module.RepetitionDetector(check_interval=1)
    text = " ".join(
        [
            "KB에 파일 업로드 기능을 찾아보겠습니다. SKILL.md를 확인합니다.",
            "KB에 파일 업로드하는 다른 방법을 찾아보겠습니다. EXECUTION-TOOLS.md를 확인합니다.",
            "KB API를 통해 직접 업로드할 수 있는지 확인하겠습니다. integration.sh를 확인합니다.",
            "document-reader 스킬의 업로드 기능을 살펴보겠습니다. 해당 스킬 파일을 읽어봅니다.",
        ]
    )

    result = detector.feed(text)

    assert result.detected is False


def test_tool_call_hash_matches_ts_json_stringify_insertion_order_fixtures() -> None:
    module = _module()

    path_first = module.ToolCallLoopDetector.hashCall(
        "FileRead",
        {"path": "a.ts", "limit": 20},
    )
    limit_first = module.ToolCallLoopDetector.hashCall(
        "FileRead",
        {"limit": 20, "path": "a.ts"},
    )

    assert path_first == "e0659b70b656331f"
    assert limit_first == "66d052efb0cd19a0"
    assert path_first != limit_first


def test_tool_call_hash_changes_for_different_tool_names_and_inputs() -> None:
    module = _module()
    base = module.ToolCallLoopDetector.hash_call("FileRead", {"path": "a.ts"})

    assert module.ToolCallLoopDetector.hash_call("Grep", {"path": "a.ts"}) != base
    assert module.ToolCallLoopDetector.hash_call("FileRead", {"path": "b.ts"}) != base


def test_tool_call_hash_excludes_ts_progress_metadata_fields() -> None:
    module = _module()
    base = module.ToolCallLoopDetector.hash_call("FileRead", {"path": "a.ts"})

    assert (
        module.ToolCallLoopDetector.hash_call(
            "FileRead",
            {
                "path": "a.ts",
                "task_progress": {"current_task": "t1"},
                "progress": "50%",
                "metadata": {"raw": "not stable"},
            },
        )
        == base
    )
    assert (
        module.ToolCallLoopDetector.hash_call(
            "FileRead",
            {
                "path": "a.ts",
                "task_progress": {"current_task": "t1"},
                "limit": 20,
                "progress": "50%",
                "metadata": {"raw": "not stable"},
            },
        )
        == "e0659b70b656331f"
    )


def test_tool_call_detector_repeated_identical_calls_trigger_soft_and_hard_thresholds() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector()

    assert detector.check("FileRead", {"path": "a.ts"}).action == "ok"
    assert detector.check("FileRead", {"path": "a.ts"}).count == 2
    soft = detector.check("FileRead", {"path": "a.ts"})
    assert soft.action == "soft_warning"
    assert soft.count == 3

    detector.check("FileRead", {"path": "a.ts"})
    hard = detector.check("FileRead", {"path": "a.ts"})
    assert hard.action == "hard_escalation"
    assert hard.count == 5


def test_tool_call_detector_alternating_calls_reset_consecutive_count() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector()

    detector.check("FileRead", {"path": "a.ts"})
    detector.check("FileRead", {"path": "a.ts"})
    reset = detector.check("FileRead", {"path": "b.ts"})
    assert reset.count == 1
    assert reset.action == "ok"

    detector.check("FileRead", {"path": "a.ts"})
    detector.check("Grep", {"pattern": "x"})
    restarted = detector.check("FileRead", {"path": "a.ts"})
    assert restarted.count == 1
    assert restarted.action == "ok"


def test_tool_call_detector_frequency_thresholds_track_per_tool_name() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector(
        frequency_soft_threshold=5,
        frequency_hard_threshold=10,
    )

    for i in range(4):
        result = detector.check("TaskGet", {"taskId": f"task_{i % 2}"})
        assert result.action == "ok"

    soft = detector.check("TaskGet", {"taskId": "task_0"})
    assert soft.action == "soft_warning"
    assert soft.frequency_count == 5
    assert soft.count == 1
    assert detector.get_tool_name_count("TaskGet") == 5

    for i in range(5, 9):
        detector.check("TaskGet", {"taskId": f"task_{i % 2}"})
    hard = detector.check("TaskGet", {"taskId": "task_1"})
    assert hard.action == "hard_escalation"
    assert hard.frequency_count == 10


def test_tool_call_detector_frequency_counts_do_not_cross_contaminate_tool_names() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector(
        frequency_soft_threshold=5,
        frequency_hard_threshold=10,
    )

    for i in range(4):
        detector.check("TaskGet", {"taskId": f"task_{i % 2}"})
        detector.check("Bash", {"command": f"echo {i}"})

    assert detector.get_tool_name_count("TaskGet") == 4
    assert detector.get_tool_name_count("Bash") == 4
    result = detector.check("TaskGet", {"taskId": "task_0"})
    assert result.action == "soft_warning"
    assert result.frequency_count == 5


def test_tool_call_detector_reset_clears_consecutive_and_frequency_state() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector(frequency_soft_threshold=3, frequency_hard_threshold=6)

    detector.check("TaskGet", {"taskId": "t1"})
    detector.check("TaskGet", {"taskId": "t2"})
    detector.reset()

    assert detector.get_tool_name_count("TaskGet") == 0
    result = detector.check("TaskGet", {"taskId": "t1"})
    assert result.action == "ok"
    assert result.count == 1


def test_tool_call_detector_default_frequency_thresholds_match_ts() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector()

    for i in range(14):
        result = detector.check("TaskGet", {"taskId": f"task_{i % 3}"})
        assert result.action == "ok"

    result = detector.check("TaskGet", {"taskId": "task_0"})
    assert result.action == "soft_warning"
    assert result.frequency_count == 15


def test_tool_call_detector_public_summary_is_sanitized() -> None:
    module = _module()
    detector = module.ToolCallLoopDetector(soft_threshold=2, hard_threshold=4)
    detector.check(
        "SecretTool",
        {"token": "secret-token", "result": "raw result", "nested": {"password": "pw"}},
    )
    result = detector.check(
        "SecretTool",
        {"token": "secret-token", "result": "raw result", "nested": {"password": "pw"}},
    )

    summary = result.to_public_summary()
    rendered = repr(summary)

    assert summary["action"] == "soft_warning"
    assert summary["count"] == 2
    assert "hash" in summary
    assert "secret-token" not in rendered
    assert "raw result" not in rendered
    assert "password" not in rendered


def test_loop_detectors_import_boundary_is_pure_local() -> None:
    script = """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.runtime.loop_detectors")
assert hasattr(module, "RepetitionDetector")
assert hasattr(module, "ToolCallLoopDetector")

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
    "supabase",
    "psycopg",
)
forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.shadow",
    "openmagi_core_agent.children",
    "openmagi_core_agent.missions",
    "openmagi_core_agent.scheduler",
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
    raise AssertionError(f"loop detector import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_loop_detectors_source_forbids_runtime_side_effect_imports() -> None:
    root = Path(__file__).parents[1]
    source = (
        root
        / "openmagi_core_agent"
        / "runtime"
        / "loop_detectors.py"
    ).read_text(encoding="utf-8")
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
        "supabase",
        "psycopg",
        "kubernetes",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.workspace",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.channels",
        "openmagi_core_agent.children",
        "openmagi_core_agent.missions",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "kubectl" not in source
    assert "exec(" not in source
    assert "eval(" not in source
