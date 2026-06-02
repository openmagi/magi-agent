from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest


def _taxonomy_module():
    try:
        return importlib.import_module("magi_agent.runtime.error_taxonomy")
    except ModuleNotFoundError as exc:
        pytest.fail(f"A6 taxonomy module is missing: {exc}")


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ({"code": "provider_error", "message": "upstream returned 500"}, "provider_error"),
        ({"exception": TimeoutError("ADK call timed out")}, "timeout"),
        ({"reason": "before_commit_blocked: validator_block"}, "validator_block"),
        ({"runner_output": {"errorCode": "redaction_failure"}}, "redaction_failure"),
        ({"runner_output": {"error": "chat_route_disabled"}}, "route_disabled"),
        ({"reason": "kill_switch_enabled"}, "kill_switch"),
        ({"code": "budget_exceeded"}, "budget_exceeded"),
        ({"reason": "model_routing_invalid: unsupported model"}, "model_routing_invalid"),
        ({"exception": RuntimeError("unexpected runner failure")}, "runner_exception"),
        ({"exception": KeyboardInterrupt("ctrl-c")}, "user_interrupt"),
        ({"code": "http_413", "message": "request entity too large"}, "context_overflow"),
        ({"runner_output": {"text": "   ", "stopReason": "end_turn"}}, "empty_output"),
        ({"runner_output": {"finishReason": "length", "text": "partial answer"}}, "truncation"),
    ),
)
def test_required_taxonomy_categories_classify_adk_outputs_and_exceptions(
    source: dict[str, object],
    expected: str,
) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)

    assert classification.category == expected
    assert classification.ts_error_code == expected
    assert classification.source == "adk_runtime"


@pytest.mark.parametrize(
    "category",
    (
        "provider_error",
        "timeout",
        "route_disabled",
        "kill_switch",
        "runner_exception",
        "context_overflow",
        "empty_output",
        "truncation",
    ),
)
def test_default_off_policy_fails_open_to_typescript_for_recoverable_runtime_gaps(
    category: str,
) -> None:
    taxonomy = _taxonomy_module()
    policy = taxonomy.RetryFallbackPolicy()
    classification = taxonomy.classify_adk_runtime_failure(code=category, message=category)

    decision = taxonomy.decide_retry_fallback(classification, policy=policy)

    assert policy.enabled is False
    assert decision.action == "restore_typescript"
    assert decision.fail_open_to_typescript is True
    assert decision.fail_closed is False
    assert decision.fallback_target == "typescript_runtime"
    assert decision.python_retry_allowed is False
    assert decision.python_max_retries == 0


@pytest.mark.parametrize(
    "category",
    (
        "validator_block",
        "redaction_failure",
        "budget_exceeded",
        "model_routing_invalid",
        "user_interrupt",
    ),
)
def test_policy_fails_closed_for_unsafe_or_terminal_categories(category: str) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(code=category, message=category)

    decision = taxonomy.decide_retry_fallback(
        classification,
        policy=taxonomy.RetryFallbackPolicy(),
    )

    assert decision.action == "fail_closed"
    assert decision.fail_open_to_typescript is False
    assert decision.fail_closed is True
    assert decision.fallback_target is None
    assert decision.python_retry_allowed is False


@pytest.mark.parametrize(
    "source",
    (
        {"exception": RuntimeError("aborted")},
        {"message": "aborted"},
    ),
)
def test_generic_aborted_stream_errors_are_retryable_provider_errors(
    source: dict[str, object],
) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)

    assert classification.category == "provider_error"
    assert classification.retryable is True
    assert classification.category != "user_interrupt"


@pytest.mark.parametrize(
    "source",
    (
        {"message": "premature close"},
        {"exception": RuntimeError("terminated")},
    ),
)
def test_typescript_retryable_stream_hints_are_provider_errors(
    source: dict[str, object],
) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)

    assert classification.category == "provider_error"
    assert classification.retryable is True
    assert classification.restore_to_typescript is True
    assert classification.fail_closed is False


@pytest.mark.parametrize(
    "source",
    (
        {"code": "AbortError"},
        {"message": "AbortError"},
        {"reason": "abort_error"},
        {"message": "AbortError: The operation was aborted"},
        {"exception": RuntimeError("AbortError: The operation was aborted")},
    ),
)
def test_explicit_abort_error_signals_are_user_interrupt_without_retry(
    source: dict[str, object],
) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)
    decision = taxonomy.decide_retry_fallback(
        classification,
        policy=taxonomy.RetryFallbackPolicy(enabled=True),
    )

    assert classification.category == "user_interrupt"
    assert classification.retryable is False
    assert decision.action == "fail_closed"
    assert decision.fail_open_to_typescript is False
    assert decision.fail_closed is True
    assert decision.python_retry_allowed is False
    assert decision.python_max_retries == 0


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ({"message": "redaction failed after timeout"}, "redaction_failure"),
        (
            {"code": "timeout", "message": "redaction_failed after upstream 503"},
            "redaction_failure",
        ),
        ({"reason": "runtime verifier timeout"}, "validator_block"),
        (
            {"code": "provider_error", "message": "validator timeout after provider retry"},
            "validator_block",
        ),
    ),
)
def test_unsafe_redaction_and_validator_signals_precede_retryable_runtime_signals(
    source: dict[str, object],
    expected: str,
) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)
    decision = taxonomy.decide_retry_fallback(
        classification,
        policy=taxonomy.RetryFallbackPolicy(enabled=True),
    )

    assert classification.category == expected
    assert classification.retryable is False
    assert classification.restore_to_typescript is False
    assert classification.fail_closed is True
    assert decision.action == "fail_closed"
    assert decision.fail_open_to_typescript is False
    assert decision.fail_closed is True
    assert decision.python_retry_allowed is False
    assert decision.python_max_retries == 0


@pytest.mark.parametrize(
    "source",
    (
        {"runner_output": {"errorCode": "public_redaction_failed"}},
        {"code": "tool_redaction_violation"},
        {"reason": "memory_redaction_failure"},
        {"message": "redaction_failed_public"},
    ),
)
def test_prefixed_or_suffixed_redaction_codes_fail_closed(source: dict[str, object]) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)
    decision = taxonomy.decide_retry_fallback(
        classification,
        policy=taxonomy.RetryFallbackPolicy(),
    )

    assert classification.category == "redaction_failure"
    assert classification.retryable is False
    assert classification.restore_to_typescript is False
    assert classification.fail_closed is True
    assert decision.action == "fail_closed"
    assert decision.fail_open_to_typescript is False
    assert decision.fail_closed is True
    assert decision.python_retry_allowed is False
    assert decision.python_max_retries == 0


@pytest.mark.parametrize(
    "source",
    (
        {"runner_output": {"errorCode": "redaction_not_verified"}},
        {"code": "public_redaction_not_verified"},
        {"reason": "safe_redaction_verification_failed"},
        {"message": "redaction verification failed after provider retry"},
    ),
)
def test_redaction_verification_codes_fail_closed(source: dict[str, object]) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)
    decision = taxonomy.decide_retry_fallback(
        classification,
        policy=taxonomy.RetryFallbackPolicy(),
    )

    assert classification.category == "redaction_failure"
    assert classification.retryable is False
    assert classification.restore_to_typescript is False
    assert classification.fail_closed is True
    assert decision.action == "fail_closed"
    assert decision.fail_open_to_typescript is False
    assert decision.fail_closed is True
    assert decision.python_retry_allowed is False
    assert decision.python_max_retries == 0


@pytest.mark.parametrize(
    "source",
    (
        {"runner_output": {"errorCode": "public_validator_block"}},
        {"code": "validator_block_public"},
        {"reason": "safe_verifier_blocked"},
        {"message": "public_before_commit_blocked"},
    ),
)
def test_prefixed_or_suffixed_validator_codes_fail_closed(source: dict[str, object]) -> None:
    taxonomy = _taxonomy_module()
    classification = taxonomy.classify_adk_runtime_failure(**source)
    decision = taxonomy.decide_retry_fallback(
        classification,
        policy=taxonomy.RetryFallbackPolicy(),
    )

    assert classification.category == "validator_block"
    assert classification.retryable is False
    assert classification.restore_to_typescript is False
    assert classification.fail_closed is True
    assert decision.action == "fail_closed"
    assert decision.fail_open_to_typescript is False
    assert decision.fail_closed is True
    assert decision.python_retry_allowed is False
    assert decision.python_max_retries == 0


def test_policy_import_and_source_stay_runner_provider_shell_and_write_free() -> None:
    module_name = "magi_agent.runtime.error_taxonomy"
    before_modules = set(sys.modules)
    module = importlib.import_module(module_name)
    new_modules = set(sys.modules) - before_modules

    forbidden_import_prefixes = (
        "google.adk",
        "google.genai",
        "openai",
        "anthropic",
        "subprocess",
        "requests",
        "httpx",
        "socket",
        "shutil",
    )
    loaded_forbidden = [
        name
        for name in new_modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_import_prefixes)
    ]
    assert loaded_forbidden == []

    source_path = Path(module.__file__).resolve()
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(
        {
            "google",
            "openai",
            "anthropic",
            "subprocess",
            "os",
            "requests",
            "httpx",
            "socket",
            "shutil",
            "pathlib",
        }
    )

    forbidden_fragments = (
        "Runner(",
        "subprocess",
        "os.system",
        ".popen",
        "kubectl",
        "vercel",
        "supabase",
        "git ",
        "pytest",
        "write_text",
        "write_bytes",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
