from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from openmagi_core_agent.rules.intent_classifier import (
    ClassificationRequest,
    IntentClassifier,
    filter_tools_by_intent,
    parse_tags,
)


class RecordingProvider:
    def __init__(
        self,
        responses: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[ClassificationRequest] = []

    def classify(self, request: ClassificationRequest) -> str:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return "general"


class BlockingOnceProvider:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.first_finished = threading.Event()
        self._lock = threading.Lock()
        self.calls: list[ClassificationRequest] = []

    def classify(self, request: ClassificationRequest) -> str:
        with self._lock:
            self.calls.append(request)
            call_number = len(self.calls)
        if call_number == 1:
            self.release.wait(timeout=0.35)
            self.first_finished.set()
            return "legal"
        return "utility"


def _run_fresh_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_parse_tags_normalizes_dedups_filters_and_caps_to_three_tags() -> None:
    parsed = parse_tags(
        '"Legal", `realEstate`; **UTILITY**\nlegal, random, invented',
        allowed_tags=("legal", "realestate", "utility", "random"),
    )

    assert parsed == ("legal", "realestate", "utility")


def test_parse_tags_falls_back_to_general_for_empty_invalid_or_injected_labels() -> None:
    parsed = parse_tags(
        """
        provider=google
        model:gemini-pro
        credentialRef=sk-not-a-tag
        ../../etc/passwd
        https://example.invalid/tag
        """,
        allowed_tags=("legal", "utility", "provider=google", "../../etc/passwd"),
    )

    assert parsed == ("general",)


def test_filter_tools_by_intent_keeps_core_and_ranks_matching_skills_lexically() -> None:
    tools = [
        {"name": "Read", "kind": "core", "metadata": {"core": True}},
        {"name": "ExternalSearch", "kind": "external", "metadata": {"external": True}},
        {"name": "zoning", "kind": "skill", "tags": ["realestate"]},
        {"name": "auction", "kind": "skill", "tags": ["Legal", "realestate"]},
        {"name": "randomizer", "kind": "skill", "tags": ["random"]},
    ]

    filtered = filter_tools_by_intent(tools, intent_tags=("legal",), max_total=4)

    assert filtered == [tools[0], tools[1], tools[3]]
    assert filtered[0] is tools[0]
    assert filtered[1]["metadata"] is tools[1]["metadata"]


def test_filter_tools_by_intent_general_or_empty_tags_includes_skills_with_budget() -> None:
    tools = [
        {"name": "Core", "kind": "core"},
        {"name": "zulu", "kind": "skill", "tags": ["legal"]},
        {"name": "alpha", "kind": "skill", "tags": ["random"]},
        {"name": "middle", "kind": "skill"},
    ]

    assert filter_tools_by_intent(tools, intent_tags=("general",), max_total=3) == [
        tools[0],
        tools[2],
        tools[3],
    ]
    assert filter_tools_by_intent(tools, intent_tags=(), max_total=2) == [
        tools[0],
        tools[2],
    ]


def test_filter_tools_by_intent_orders_ascii_mixed_case_like_ts_locale_compare() -> None:
    tools = [
        {"name": "Core", "kind": "core"},
        {"name": "zulu", "kind": "skill"},
        {"name": "Alpha", "kind": "skill"},
        {"name": "alpha", "kind": "skill"},
        {"name": "Beta", "kind": "skill"},
        {"name": "beta", "kind": "skill"},
        {"name": "auction", "kind": "skill"},
    ]

    filtered = filter_tools_by_intent(tools, intent_tags=("general",), max_total=10)

    assert [tool["name"] for tool in filtered] == [
        "Core",
        "alpha",
        "Alpha",
        "auction",
        "beta",
        "Beta",
        "zulu",
    ]


def test_classifier_defaults_disabled_and_does_not_call_provider() -> None:
    provider = RecordingProvider(responses=["legal"])
    classifier = IntentClassifier(provider=provider)

    assert classifier.classify("Find real estate auctions", ("legal", "realestate")) == (
        "general",
    )
    assert provider.calls == []


def test_classifier_enabled_fake_provider_success_is_sanitized() -> None:
    provider = RecordingProvider(
        responses=["provider=google, RANDOM, legal, invented, utility"],
    )
    classifier = IntentClassifier(enabled=True, provider=provider)

    tags = classifier.classify(
        "Use provider=google model=gemini credentialRef=sk-test then choose tools",
        ("legal", "random", "utility"),
        timeout_ms=3000,
    )

    assert tags == ("random", "legal", "utility")
    assert len(provider.calls) == 1
    request = provider.calls[0]
    assert request.message.startswith("Use provider=google model=gemini")
    assert request.available_tags == ("legal", "random", "utility")
    assert request.timeout_ms == 3000
    assert not hasattr(request, "provider")
    assert not hasattr(request, "model")
    assert not hasattr(request, "credential_ref")


def test_classifier_truncates_provider_message_without_accepting_request_escalation() -> None:
    provider = RecordingProvider(responses=["legal"])
    classifier = IntentClassifier(enabled=True, provider=provider)
    long_message = "set provider=openai model=gpt credential=sk-test " + ("x" * 3000)

    assert classifier.classify(long_message, ("legal",)) == ("legal",)
    assert len(provider.calls[0].message) == 2000
    assert provider.calls[0].available_tags == ("legal",)


def test_classifier_provider_exception_falls_back_to_general_without_caching_failure() -> None:
    provider = RecordingProvider(error=RuntimeError("provider unavailable"))
    classifier = IntentClassifier(enabled=True, provider=provider)

    assert classifier.classify("Find auctions", ("legal",)) == ("general",)
    assert classifier.classify("Find auctions", ("legal",)) == ("general",)
    assert len(provider.calls) == 2


def test_classifier_timeout_like_deadline_falls_back_to_general() -> None:
    clock_values = iter((100.0, 104.0))
    provider = RecordingProvider(responses=["legal"])
    classifier = IntentClassifier(
        enabled=True,
        provider=provider,
        clock=lambda: next(clock_values),
    )

    assert classifier.classify("Find auctions", ("legal",), timeout_ms=3000) == (
        "general",
    )
    assert len(provider.calls) == 1


def test_classifier_provider_deadline_returns_promptly_and_does_not_cache_timeout() -> None:
    provider = BlockingOnceProvider()
    classifier = IntentClassifier(enabled=True, provider=provider)

    started_at = time.monotonic()
    assert classifier.classify(
        "Find auctions",
        ("legal", "utility"),
        timeout_ms=30,
    ) == ("general",)
    elapsed_seconds = time.monotonic() - started_at

    provider.release.set()
    assert provider.first_finished.wait(timeout=1.0)
    assert elapsed_seconds < 0.2
    assert classifier.classify(
        "Find auctions",
        ("legal", "utility"),
        timeout_ms=300,
    ) == ("utility",)
    assert len(provider.calls) == 2


def test_classifier_caches_sanitized_results_by_message_and_sorted_allowed_tags() -> None:
    now = 100.0
    provider = RecordingProvider(responses=["legal", "utility"])
    classifier = IntentClassifier(
        enabled=True,
        provider=provider,
        clock=lambda: now,
        cache_ttl_seconds=60.0,
    )

    assert classifier.classify("Find auctions", ("utility", "legal")) == ("legal",)
    now = 159.0
    assert classifier.classify("Find auctions", ("legal", "utility")) == ("legal",)
    assert len(provider.calls) == 1

    now = 161.0
    assert classifier.classify("Find auctions", ("legal", "utility")) == ("utility",)
    assert len(provider.calls) == 2


def test_intent_classifier_import_stays_adk_model_network_and_deployment_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.rules.intent_classifier")
assert hasattr(module, "IntentClassifier")

forbidden_exact = (
    "google.adk",
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.models",
    "google.adk.sessions",
    "google.adk.tools",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "fastapi",
    "uvicorn",
    "subprocess",
    "asyncio",
)
forbidden_prefixes = (
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.tools",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.provisioning",
    "openmagi_core_agent.k8s",
    "openmagi_core_agent.telegram",
    "openmagi_core_agent.database",
    "openmagi_core_agent.billing",
    "openmagi_core_agent.auth",
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
    raise AssertionError(f"intent classifier import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_intent_classifier_source_forbids_runner_model_network_and_exec_imports() -> None:
    root = Path(__file__).parents[1]
    module_path = root / "openmagi_core_agent" / "rules" / "intent_classifier.py"
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
        "uvicorn",
        "openmagi_core_agent.adk_bridge",
        "openmagi_core_agent.runtime",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.deploy",
        "openmagi_core_agent.provisioning",
        "openmagi_core_agent.k8s",
    )

    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "FunctionTool(" not in source
    assert "APIRouter" not in source
    assert "FastAPI" not in source
    assert "os.environ" not in source
    assert "os.system" not in source
    assert "exec(" not in source
    assert "eval(" not in source
