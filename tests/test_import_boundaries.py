from __future__ import annotations

import subprocess
import sys

import pytest


def _run_fresh_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "module_name",
    (
        "openmagi_core_agent.tools.manifest",
        "openmagi_core_agent.tools.registry",
        "openmagi_core_agent.hooks.manifest",
    ),
)
def test_openmagi_modules_import_in_fresh_process(module_name: str) -> None:
    completed = _run_fresh_python(
        "import importlib, sys; importlib.import_module(sys.argv[1])",
        module_name,
    )

    assert completed.returncode == 0, completed.stderr


def test_adk_bridge_package_import_does_not_load_tool_adapter() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

importlib.import_module("openmagi_core_agent.adk_bridge")
forbidden_modules = (
    "openmagi_core_agent.adk_bridge.policy_boundary",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.registry",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"adk_bridge package import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_adk_callback_adapter_import_stays_isolated_in_fresh_process() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

importlib.import_module("openmagi_core_agent.adk_bridge.callback_adapter")
forbidden_modules = (
    "openmagi_core_agent.adk_bridge.policy_boundary",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.registry",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"callback_adapter import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_adk_tool_adapter_imports_explicitly_in_fresh_process() -> None:
    completed = _run_fresh_python(
        """
import importlib

module = importlib.import_module("openmagi_core_agent.adk_bridge.tool_adapter")
assert hasattr(module, "build_adk_function_tool")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_deterministic_routing_import_stays_runner_and_runtime_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.routing.deterministic")
assert hasattr(module, "build_baseline_shadow_route")

forbidden_modules = (
    "google.adk.runners",
    "google.adk.runners.runner",
    "google.adk.tools",
    "google.adk.tools.function_tool",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport.chat",
)
loaded = [module_name for module_name in forbidden_modules if module_name in sys.modules]
if loaded:
    raise AssertionError(f"deterministic routing import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_provider_execution_import_stays_adk_toolhost_memory_transport_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

execution = importlib.import_module("openmagi_core_agent.runtime.provider_execution")
receipts = importlib.import_module("openmagi_core_agent.runtime.provider_receipts")
assert hasattr(execution, "ProviderExecutionBoundary")
assert hasattr(receipts, "ProviderReceipt")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.local_runner",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.kernel",
    "openmagi_core_agent.tools.registry",
    "openmagi_core_agent.tools.manifest",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.web_acquisition",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"provider execution import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_memory_adk_bridge_import_stays_adk_runtime_transport_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

before = set(sys.modules)
module = importlib.import_module("openmagi_core_agent.memory.adk_bridge")
assert hasattr(module, "ADKMemoryServiceBridge")

forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "google.cloud",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    module_name
    for module_name in sys.modules
    if module_name not in before
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"memory ADK bridge import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_openmagi_runtime_default_construction_does_not_inspect_or_import_adk_primitives() -> None:
    completed = _run_fresh_python(
        """
import sys

before = set(sys.modules)
from openmagi_core_agent.config.models import BuildInfo, RuntimeConfig
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime

runtime = OpenMagiRuntime(
    config=RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
    )
)
assert runtime.status()["adk"]["invoked"] is False

forbidden_prefixes = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "google.adk.memory",
    "google.adk.artifacts",
    "google.adk.evaluation",
    "google.cloud",
    "requests",
    "httpx",
)
loaded = [
    module_name
    for module_name in sys.modules
    if module_name not in before
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"runtime default construction loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_file_delivery_import_stays_adk_transport_channel_sdk_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.artifacts.file_delivery")
assert hasattr(module, "FileDeliveryBoundary")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.web_acquisition",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"file delivery import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_channel_dispatch_and_push_imports_stay_sdk_transport_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

dispatcher = importlib.import_module("openmagi_core_agent.channels.dispatcher")
push = importlib.import_module("openmagi_core_agent.channels.push_delivery")
assert hasattr(dispatcher, "ChannelDispatcher")
assert hasattr(push, "PushDeliveryBoundary")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.web_acquisition",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"channel dispatch/push import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_telegram_adapter_import_stays_sdk_transport_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.channels.telegram_adapter")
assert hasattr(module, "TelegramAdapterBoundary")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.web_acquisition",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "telegram",
    "discord",
    "aiohttp",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"telegram adapter import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_discord_adapter_import_stays_sdk_transport_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.channels.discord_adapter")
assert hasattr(module, "DiscordAdapterBoundary")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.browser",
    "openmagi_core_agent.web_acquisition",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "openmagi_core_agent.runtime_selector",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.k8s",
    "telegram",
    "discord",
    "nextcord",
    "kubernetes",
    "aiohttp",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"discord adapter import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_tool_manifest_import_stays_adk_runtime_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.tools.manifest")
assert hasattr(module, "ToolManifest")

forbidden_modules = (
    "google.adk",
    "google.adk.runners",
    "google.adk.tools",
    "google.adk.tools.function_tool",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
)
loaded = [module_name for module_name in forbidden_modules if module_name in sys.modules]
if loaded:
    raise AssertionError(f"tool manifest import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_tool_registry_import_stays_adk_and_bridge_adapter_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.tools.registry")
assert hasattr(module, "ToolRegistry")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"tool registry import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_evidence_ledger_import_stays_adk_runner_runtime_and_route_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.evidence.ledger")
assert hasattr(module, "EvidenceLedger")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"evidence ledger import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_evidence_subagent_import_stays_adk_runtime_child_execution_and_route_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.evidence.subagent")
assert hasattr(module, "ChildEvidenceEnvelope")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.session",
    "openmagi_core_agent.artifact",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"evidence subagent import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_child_runtime_envelope_import_stays_adk_toolhost_memory_and_workspace_mutation_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.evidence.child_runtime_envelope")
assert hasattr(module, "ChildRuntimeEnvelope")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.tools.registry",
    "openmagi_core_agent.memory",
    "openmagi_core_agent.workspace.mutation",
    "openmagi_core_agent.workspace.adoption",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"child runtime envelope import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_observable_process_reward_import_stays_adk_runner_runtime_and_route_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.harness.process_reward")
assert hasattr(module, "score_observable_process_events")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"process reward import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_inference_scaling_import_stays_adk_runner_routing_proxy_and_billing_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.harness.inference_scaling")
assert hasattr(module, "build_scaling_policy_decision")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.transport.plugins",
    "openmagi_core_agent.config.models",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"inference scaling import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_parallel_execution_import_stays_adk_runner_tool_execution_scheduler_route_workspace_and_canary_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.harness.parallel_execution")
assert hasattr(module, "build_parallel_tool_policy_decision")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.runtime.openmagi_runtime",
    "openmagi_core_agent.tools.dispatcher",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"parallel execution import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "module_name",
    (
        "openmagi_core_agent.runtime.model_tiers",
        "openmagi_core_agent.recipes.reliability_policy",
        "openmagi_core_agent.runtime.phase_routing",
        "openmagi_core_agent.runtime.context_budget",
        "openmagi_core_agent.runtime.request_shape",
        "openmagi_core_agent.runtime.streaming",
        "openmagi_core_agent.evidence.calculation_policy",
        "openmagi_core_agent.runtime.evidence_first_projection",
        "openmagi_core_agent.runtime.uncertainty_policy",
        "openmagi_core_agent.evidence.final_output_gate",
        "openmagi_core_agent.harness.long_context_eval",
        "openmagi_core_agent.runtime.reliability_budget",
        "openmagi_core_agent.recipes.materializer",
    ),
)
def test_reliability_policy_modules_import_without_live_runtime_side_effects(
    module_name: str,
) -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module(sys.argv[1])

forbidden_prefixes = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "aiohttp",
    "selenium",
    "playwright",
    "kubernetes",
    "psycopg",
    "asyncpg",
    "supabase",
    "redis",
    "pymongo",
    "telegram",
    "subprocess",
    "socket",
    "git",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.local_runner",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.transport.chat",
    "openmagi_core_agent.transport.tools",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.workspace",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"reliability policy import loaded forbidden modules: {loaded}")
""",
        module_name,
    )

    assert completed.returncode == 0, completed.stderr
