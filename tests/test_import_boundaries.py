from __future__ import annotations

import ast
from pathlib import Path
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
        "magi_agent.tools.manifest",
        "magi_agent.tools.registry",
        "magi_agent.hooks.manifest",
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

importlib.import_module("magi_agent.adk_bridge")
forbidden_modules = (
    "magi_agent.adk_bridge.policy_boundary",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
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

importlib.import_module("magi_agent.adk_bridge.callback_adapter")
forbidden_modules = (
    "magi_agent.adk_bridge.policy_boundary",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
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

module = importlib.import_module("magi_agent.adk_bridge.tool_adapter")
assert hasattr(module, "build_adk_function_tool")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_provider_execution_import_stays_adk_toolhost_memory_transport_and_network_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

execution = importlib.import_module("magi_agent.runtime.provider_execution")
receipts = importlib.import_module("magi_agent.runtime.provider_receipts")
assert hasattr(execution, "ProviderExecutionBoundary")
assert hasattr(receipts, "ProviderReceipt")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.kernel",
    "magi_agent.tools.registry",
    "magi_agent.tools.manifest",
    "magi_agent.transport",
    "magi_agent.memory",
    "magi_agent.channels",
    "magi_agent.browser",
    "magi_agent.web_acquisition",
    "magi_agent.deploy",
    "magi_agent.canary",
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
module = importlib.import_module("magi_agent.memory.adk_bridge")
assert hasattr(module, "ADKMemoryServiceBridge")

forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "google.cloud",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.canary",
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
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

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

module = importlib.import_module("magi_agent.artifacts.file_delivery")
assert hasattr(module, "FileDeliveryBoundary")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.transport",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.web_acquisition",
    "magi_agent.deploy",
    "magi_agent.canary",
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

dispatcher = importlib.import_module("magi_agent.channels.dispatcher")
push = importlib.import_module("magi_agent.channels.push_delivery")
assert hasattr(dispatcher, "ChannelDispatcher")
assert hasattr(push, "PushDeliveryBoundary")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.transport",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.web_acquisition",
    "magi_agent.deploy",
    "magi_agent.canary",
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

module = importlib.import_module("magi_agent.channels.telegram_adapter")
assert hasattr(module, "TelegramAdapterBoundary")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.transport",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.web_acquisition",
    "magi_agent.deploy",
    "magi_agent.canary",
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

module = importlib.import_module("magi_agent.channels.discord_adapter")
assert hasattr(module, "DiscordAdapterBoundary")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.web_acquisition",
    "magi_agent.deploy",
    "magi_agent.canary",
    "magi_agent.runtime_selector",
    "magi_agent.chat_proxy",
    "magi_agent.k8s",
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

module = importlib.import_module("magi_agent.tools.manifest")
assert hasattr(module, "ToolManifest")

forbidden_modules = (
    "google.adk",
    "google.adk.runners",
    "google.adk.tools",
    "google.adk.tools.function_tool",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
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

module = importlib.import_module("magi_agent.tools.registry")
assert hasattr(module, "ToolRegistry")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
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

module = importlib.import_module("magi_agent.evidence.ledger")
assert hasattr(module, "EvidenceLedger")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
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

module = importlib.import_module("magi_agent.evidence.subagent")
assert hasattr(module, "ChildEvidenceEnvelope")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.session",
    "magi_agent.artifact",
    "magi_agent.deploy",
    "magi_agent.canary",
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


def test_child_runtime_envelope_import_stays_adk_toolhost_memory_and_workspace_mutation_free() -> (
    None
):
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.evidence.child_runtime_envelope")
assert hasattr(module, "ChildRuntimeEnvelope")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.memory",
    "magi_agent.workspace.mutation",
    "magi_agent.workspace.adoption",
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


def test_inference_scaling_import_stays_adk_runner_routing_proxy_and_billing_free() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.harness.inference_scaling")
assert hasattr(module, "build_scaling_policy_decision")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.transport.plugins",
    "magi_agent.config.models",
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


def test_parallel_execution_import_stays_adk_runner_tool_execution_scheduler_route_workspace_and_canary_free() -> (
    None
):
    completed = _run_fresh_python(
        """
import importlib
import sys

module = importlib.import_module("magi_agent.harness.parallel_execution")
assert hasattr(module, "build_parallel_tool_policy_decision")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.workspace",
    "magi_agent.deploy",
    "magi_agent.canary",
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
        "magi_agent.runtime.model_tiers",
        "magi_agent.recipes.reliability_policy",
        "magi_agent.runtime.phase_routing",
        "magi_agent.runtime.context_budget",
        "magi_agent.runtime.request_shape",
        "magi_agent.runtime.streaming",
        "magi_agent.evidence.calculation_policy",
        "magi_agent.runtime.evidence_first_projection",
        "magi_agent.runtime.uncertainty_policy",
        "magi_agent.evidence.final_output_gate",
        "magi_agent.harness.long_context_eval",
        "magi_agent.runtime.reliability_budget",
        "magi_agent.recipes.materializer",
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
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.channels",
    "magi_agent.workspace",
    "magi_agent.deploy",
    "magi_agent.canary",
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


_EXECUTION_AUTHORITY_FORBIDDEN_IMPORT_PREFIXES = (
    "aiohttp",
    "anthropic",
    "asyncio",
    "boto3",
    "builtins",
    "concurrent.futures",
    "discord",
    "ftplib",
    "google.adk",
    "http.client",
    "httpx",
    "importlib",
    "kubernetes",
    "multiprocessing",
    "openai",
    "psycopg",
    "redis",
    "requests",
    "socket",
    "stripe",
    "subprocess",
    "supabase",
    "telegram",
    "urllib.request",
    "urllib3",
    "magi_agent.adk_bridge",
    "magi_agent.browser",
    "magi_agent.channels",
    "magi_agent.cli",
    "magi_agent.customize",
    "magi_agent.egress_proxy",
    "magi_agent.engine",
    "magi_agent.gateway",
    "magi_agent.gates",
    "magi_agent.models",
    "magi_agent.permissions",
    "magi_agent.plugins",
    "magi_agent.providers",
    "magi_agent.routing",
    "magi_agent.runtime",
    "magi_agent.sandbox",
    "magi_agent.tools",
    "magi_agent.transport",
    "magi_agent.web_acquisition",
    "magi_agent.web_dashboard",
)


def _execution_authority_import_targets(
    node: ast.Import | ast.ImportFrom,
    *,
    source_path: Path,
    package: Path,
) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if node.level == 0:
        base = node.module or ""
    else:
        repository_root = package.parent.parent
        package_parts = source_path.relative_to(repository_root).parts[:-1]
        parents_to_remove = node.level - 1
        if parents_to_remove >= len(package_parts):
            return ("<relative-import-beyond-package>",)
        base_parts = package_parts[: len(package_parts) - parents_to_remove]
        if node.module:
            base = ".".join((*base_parts, *node.module.split(".")))
        else:
            base = ".".join(base_parts)

    candidates = [base] if base else []
    candidates.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return tuple(dict.fromkeys(candidates))


def _execution_authority_forbidden_call_target(target: str) -> bool:
    if target in {
        "__import__",
        "builtins.__import__",
        "eval",
        "builtins.eval",
        "exec",
        "builtins.exec",
        "importlib.import_module",
    }:
        return True
    if target in {
        "builtins.<dynamic-attribute>",
        "importlib.<dynamic-attribute>",
    }:
        return True
    if not target.startswith("os."):
        return False
    member = target.removeprefix("os.")
    if member == "<dynamic-attribute>":
        return True
    normalized_member = member.lstrip("_")
    return (
        normalized_member
        in {"fork", "fork1", "forkpty", "popen", "startfile", "system", "vfork"}
        or normalized_member.startswith("exec")
        or normalized_member.startswith("spawn")
        or normalized_member.startswith("posix_spawn")
    )


class _ExecutionAuthorityCallVisitor(ast.NodeVisitor):
    _BUILTIN_CALLABLES = frozenset(("__import__", "eval", "exec", "getattr"))

    def __init__(self, *, source_path: Path, package: Path) -> None:
        self._source_path = source_path
        self._package = package
        self._scopes: list[dict[str, str | None]] = [{}]
        self._scope_kinds: list[str] = ["module"]
        self._seen: set[tuple[int, str]] = set()
        self.violations: list[str] = []

    def _lookup(self, name: str) -> str | None:
        callable_above = False
        for scope, kind in zip(
            reversed(self._scopes),
            reversed(self._scope_kinds),
            strict=True,
        ):
            if kind == "class" and callable_above:
                continue
            if name in scope:
                return scope[name]
            if kind in {"function", "lambda"}:
                callable_above = True
        return name if name in self._BUILTIN_CALLABLES else None

    def _expression_target(self, expression: ast.expr) -> str | None:
        if isinstance(expression, ast.Name):
            return self._lookup(expression.id)
        if isinstance(expression, ast.Attribute):
            base = self._expression_target(expression.value)
            return f"{base}.{expression.attr}" if base else None
        if isinstance(expression, ast.Call):
            getter = self._expression_target(expression.func)
            if getter not in {"getattr", "builtins.getattr"} or len(expression.args) < 2:
                return None
            base = self._expression_target(expression.args[0])
            member = expression.args[1]
            if base and isinstance(member, ast.Constant) and isinstance(member.value, str):
                return f"{base}.{member.value}"
            if base in {"builtins", "importlib", "os"}:
                return f"{base}.<dynamic-attribute>"
        return None

    def _record(self, node: ast.expr | ast.stmt, target: str | None) -> None:
        if target is None or not _execution_authority_forbidden_call_target(target):
            return
        key = node.lineno, target
        if key in self._seen:
            return
        self._seen.add(key)
        relative_source = self._source_path.relative_to(self._package).as_posix()
        self.violations.append(f"{relative_source}:{node.lineno}:{target}")

    def _bind_assignment(self, target: ast.expr, value: str | None) -> None:
        if isinstance(target, ast.Name):
            self._scopes[-1][target.id] = value
        elif isinstance(target, ast.Starred):
            self._bind_assignment(target.value, None)
        elif isinstance(target, ast.Tuple | ast.List):
            for item in target.elts:
                self._bind_assignment(item, None)

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            binding = imported.asname or imported.name.split(".", maxsplit=1)[0]
            value = imported.name if imported.asname else binding
            self._scopes[-1][binding] = value

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        targets = _execution_authority_import_targets(
            node,
            source_path=self._source_path,
            package=self._package,
        )
        if not targets:
            return
        base = targets[0]
        for imported in node.names:
            if imported.name == "*":
                continue
            binding = imported.asname or imported.name
            value = f"{base}.{imported.name}" if base else imported.name
            self._scopes[-1][binding] = value

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        value = self._expression_target(node.value)
        self._record(node, value)
        for target in node.targets:
            self._bind_assignment(target, value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        value = None
        if node.value is not None:
            self.visit(node.value)
            value = self._expression_target(node.value)
            self._record(node, value)
        self._bind_assignment(node.target, value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        value = self._expression_target(node.value)
        self._record(node, value)
        self._bind_assignment(node.target, value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._bind_assignment(node.target, None)

    def visit_Call(self, node: ast.Call) -> None:
        self._record(node, self._expression_target(node.func))
        self._record(node, self._expression_target(node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            target = self._lookup(node.id)
            if target in {"__import__", "eval", "exec"}:
                self._record(node, target)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._bind_assignment(node.target, None)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_assignment(item.optional_vars, None)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        previous = self._scopes[-1].get(node.name) if node.name is not None else None
        had_previous = node.name in self._scopes[-1] if node.name is not None else False
        if node.name is not None:
            self._scopes[-1][node.name] = None
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            if node.name is not None:
                if had_previous:
                    self._scopes[-1][node.name] = previous
                else:
                    self._scopes[-1].pop(node.name, None)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        values: tuple[ast.expr, ...],
    ) -> None:
        if not generators:
            for value in values:
                self.visit(value)
            return

        self.visit(generators[0].iter)
        self._scopes.append({})
        self._scope_kinds.append("comprehension")
        try:
            self._bind_assignment(generators[0].target, None)
            for condition in generators[0].ifs:
                self.visit(condition)
            for generator in generators[1:]:
                self.visit(generator.iter)
                self._bind_assignment(generator.target, None)
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)
        finally:
            self._scope_kinds.pop()
            self._scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scopes[-1][node.name] = None
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

        function_scope: dict[str, str | None] = {}
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            function_scope[argument.arg] = None
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for variadic in (node.args.vararg, node.args.kwarg):
            if variadic is not None:
                function_scope[variadic.arg] = None
                if variadic.annotation is not None:
                    self.visit(variadic.annotation)

        self._scopes.append(function_scope)
        self._scope_kinds.append("function")
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._scope_kinds.pop()
            self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        lambda_scope: dict[str, str | None] = {}
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        for argument in arguments:
            lambda_scope[argument.arg] = None
        for variadic in (node.args.vararg, node.args.kwarg):
            if variadic is not None:
                lambda_scope[variadic.arg] = None
        self._scopes.append(lambda_scope)
        self._scope_kinds.append("lambda")
        try:
            self.visit(node.body)
        finally:
            self._scope_kinds.pop()
            self._scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

        self._scopes.append({})
        self._scope_kinds.append("class")
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._scope_kinds.pop()
            self._scopes.pop()
        self._scopes[-1][node.name] = None


def _execution_authority_source_violations(package: Path) -> list[str]:
    violations: list[str] = []

    for source_path in sorted(package.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                imports = _execution_authority_import_targets(
                    node,
                    source_path=source_path,
                    package=package,
                )
                for imported in imports:
                    if any(
                        imported == prefix or imported.startswith(f"{prefix}.")
                        for prefix in _EXECUTION_AUTHORITY_FORBIDDEN_IMPORT_PREFIXES
                    ):
                        relative_source = source_path.relative_to(package).as_posix()
                        violations.append(f"{relative_source}:{node.lineno}:{imported}")
                    if imported == "<relative-import-beyond-package>":
                        relative_source = source_path.relative_to(package).as_posix()
                        violations.append(f"{relative_source}:{node.lineno}:{imported}")
        call_visitor = _ExecutionAuthorityCallVisitor(
            source_path=source_path,
            package=package,
        )
        call_visitor.visit(tree)
        violations.extend(call_visitor.violations)
    return violations


def test_execution_authority_source_has_no_live_runtime_or_dynamic_imports() -> None:
    package = Path(__file__).parents[1] / "magi_agent" / "execution_authority"
    violations = _execution_authority_source_violations(package)

    assert not violations, "execution-authority import violations: " + ", ".join(violations)


def test_execution_authority_source_guard_recurses_and_resolves_relative_escapes(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    nested = package / "nested"
    nested.mkdir(parents=True)
    (package / "relative_escape.py").write_text(
        "from ..engine import Driver\n",
        encoding="utf-8",
    )
    (nested / "dynamic_escape.py").write_text(
        "eval('1 + 1')\nexec('value = 1')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("relative_escape.py:1:magi_agent.engine" in item for item in violations)
    assert any("nested/dynamic_escape.py:1:eval" in item for item in violations)
    assert any("nested/dynamic_escape.py:2:exec" in item for item in violations)


def test_execution_authority_source_guard_resolves_lazy_imported_modules(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "lazy_escape.py").write_text(
        "def load_forbidden_modules():\n"
        "    from magi_agent import engine\n"
        "    from urllib import request\n",
        encoding="utf-8",
    )
    (package / "safe_symbol.py").write_text(
        "def build_field():\n    from pydantic import Field\n    return Field\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("lazy_escape.py:2:magi_agent.engine" in item for item in violations)
    assert any("lazy_escape.py:3:urllib.request" in item for item in violations)
    assert not any("safe_symbol.py" in item for item in violations)


def test_execution_authority_static_guard_is_primary_when_runtime_import_is_cached(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "cached_escape.py").write_text(
        "import subprocess\n",
        encoding="utf-8",
    )

    # tests/test_import_boundaries.py imported subprocess before this test was
    # collected. A sys.modules delta cannot see that cached import, so the
    # source policy is the primary boundary and the runtime delta is only a
    # defense-in-depth signal.
    assert "subprocess" in sys.modules

    violations = _execution_authority_source_violations(package)

    assert any("cached_escape.py:1:subprocess" in item for item in violations)


@pytest.mark.parametrize("builtin_name", ("__import__", "eval", "exec"))
def test_execution_authority_source_guard_rejects_sensitive_callable_references(
    tmp_path: Path,
    builtin_name: str,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "higher_order_escape.py").write_text(
        f"def escape(callback={builtin_name}):\n"
        "    return callback('subprocess')\n"
        f"callbacks = [{builtin_name}]\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any(
        f"higher_order_escape.py:1:{builtin_name}" in item for item in violations
    )
    assert any(
        f"higher_order_escape.py:3:{builtin_name}" in item for item in violations
    )


def test_execution_authority_source_guard_rejects_computed_getattr_escapes(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "computed_getattr_escape.py").write_text(
        "import builtins\n"
        "import os\n"
        "load = getattr(builtins, '__' + 'import__')\n"
        "launch = getattr(os, 'sys' + 'tem')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("computed_getattr_escape.py:1:builtins" in item for item in violations)
    assert any(
        "computed_getattr_escape.py:3:builtins.<dynamic-attribute>" in item
        for item in violations
    )
    assert any(
        "computed_getattr_escape.py:4:os.<dynamic-attribute>" in item
        for item in violations
    )


@pytest.mark.parametrize(
    "member",
    (
        "_execvpe",
        "_spawnvef",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "fork1",
        "forkpty",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "startfile",
        "system",
        "vfork",
    ),
)
def test_execution_authority_source_guard_rejects_os_process_launch_family(
    tmp_path: Path,
    member: str,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "process_escape.py").write_text(
        f"import os\nos.{member}('escaped')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any(f"process_escape.py:2:os.{member}" in item for item in violations)


def test_execution_authority_source_guard_rejects_indirect_import_and_process_escape(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "indirect_escape.py").write_text(
        "import builtins as runtime_builtins\n"
        "import os as filesystem\n"
        "load = getattr(runtime_builtins, '__import__')\n"
        "load('urllib.request')\n"
        "filesystem.system('echo escaped')\n",
        encoding="utf-8",
    )
    (package / "allowed_leafs.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from pydantic import BaseModel\n"
        "os.fspath(Path('.'))\n"
        "os.stat('.')\n"
        "BaseModel.model_validate({})\n",
        encoding="utf-8",
    )
    (package / "aliased_escape.py").write_text(
        "from builtins import __import__ as import_anything\n"
        "from os import system as launch\n"
        "import_anything('subprocess')\n"
        "launch('echo escaped')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("indirect_escape.py:3:builtins.__import__" in item for item in violations)
    assert any("indirect_escape.py:5:os.system" in item for item in violations)
    assert any("aliased_escape.py:3:builtins.__import__" in item for item in violations)
    assert any("aliased_escape.py:4:os.system" in item for item in violations)
    assert not any("allowed_leafs.py" in item for item in violations)


def test_execution_authority_source_guard_tracks_assignment_aliases_without_shadow_false_positive(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "assignment_escape.py").write_text(
        "import builtins\n"
        "import os\n"
        "resolve = getattr\n"
        "loader = resolve(builtins, '__import__')\n"
        "launch = os.system\n"
        "loader('urllib.request')\n"
        "launch('echo escaped')\n",
        encoding="utf-8",
    )
    (package / "shadowed_domain.py").write_text(
        "def render(os):\n"
        "    os.system('a harmless domain method')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("assignment_escape.py:4:builtins.__import__" in item for item in violations)
    assert any("assignment_escape.py:5:os.system" in item for item in violations)
    assert not any("shadowed_domain.py" in item for item in violations)


def test_execution_authority_source_guard_honors_block_and_comprehension_shadows(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "for_shadow.py").write_text(
        "import os\n"
        "def render(values):\n"
        "    for os in values:\n"
        "        os.system('a harmless domain method')\n",
        encoding="utf-8",
    )
    (package / "with_shadow.py").write_text(
        "import os\n"
        "def render(factory):\n"
        "    with factory() as os:\n"
        "        os.system('a harmless domain method')\n",
        encoding="utf-8",
    )
    (package / "except_shadow.py").write_text(
        "import os\n"
        "def render():\n"
        "    try:\n"
        "        raise RuntimeError\n"
        "    except RuntimeError as os:\n"
        "        os.system('a harmless domain method')\n",
        encoding="utf-8",
    )
    (package / "comprehension_shadow.py").write_text(
        "import os\n"
        "def render(values):\n"
        "    return [os.system('a harmless domain method') for os in values]\n",
        encoding="utf-8",
    )
    (package / "builtin_shadow.py").write_text(
        "def render(values, manager):\n"
        "    for exec in values:\n"
        "        exec('a harmless domain callback')\n"
        "    with manager() as eval:\n"
        "        eval('a harmless domain callback')\n"
        "    return [__import__('domain') for __import__ in values]\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert not violations


def test_execution_authority_source_guard_restores_comprehension_outer_scope(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "comprehension_escape.py").write_text(
        "import os\n"
        "def launch(values):\n"
        "    [value for os in values]\n"
        "    os.system('echo escaped')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("comprehension_escape.py:4:os.system" in item for item in violations)


def test_execution_authority_source_guard_isolates_class_scope(
    tmp_path: Path,
) -> None:
    package = tmp_path / "magi_agent" / "execution_authority"
    package.mkdir(parents=True)
    (package / "class_scope_escape.py").write_text(
        "import os\n"
        "class Metadata:\n"
        "    os = object()\n"
        "os.system('echo escaped')\n",
        encoding="utf-8",
    )
    (package / "shadowing_class.py").write_text(
        "import os\n"
        "class os:\n"
        "    @staticmethod\n"
        "    def system(value):\n"
        "        return value\n"
        "os.system('a harmless domain method')\n",
        encoding="utf-8",
    )
    (package / "method_global_escape.py").write_text(
        "import os\n"
        "class Metadata:\n"
        "    os = object()\n"
        "    @staticmethod\n"
        "    def launch():\n"
        "        os.system('echo escaped')\n",
        encoding="utf-8",
    )

    violations = _execution_authority_source_violations(package)

    assert any("class_scope_escape.py:4:os.system" in item for item in violations)
    assert any("method_global_escape.py:6:os.system" in item for item in violations)
    assert not any("shadowing_class.py" in item for item in violations)


def test_execution_authority_runtime_import_delta_adds_no_live_engine_network_or_process_modules() -> None:
    # Defense in depth only: a sys.modules delta cannot detect a forbidden
    # module that a dependency cached before the measurement. The recursive
    # static source guard above is the primary execution-authority boundary.
    completed = _run_fresh_python(
        """
import importlib
import sys

# Pydantic itself imports several stdlib process/network helpers. Preload the
# contract dependency leaves before measuring execution-authority's own delta.
importlib.import_module("pydantic")
importlib.import_module("magi_agent.ops.authority")
importlib.import_module("magi_agent.ops.safety")
before = set(sys.modules)

for name in (
    "magi_agent.execution_authority",
    "magi_agent.execution_authority.state_machine",
    "magi_agent.execution_authority.contracts",
    "magi_agent.execution_authority.ports",
    "magi_agent.execution_authority.canonicalization",
    "magi_agent.execution_authority.broker",
    "magi_agent.execution_authority.evidence_closure",
    "magi_agent.execution_authority.evidence_lineage",
    "magi_agent.execution_authority.execution_material",
    "magi_agent.execution_authority.journal",
    "magi_agent.execution_authority.journal_integrity",
    "magi_agent.execution_authority.journal_sqlite",
    "magi_agent.execution_authority.migrations",
    "magi_agent.execution_authority.observation_contracts",
    "magi_agent.execution_authority.projection_registry",
    "magi_agent.execution_authority.recovery_protocol",
    "magi_agent.execution_authority.sandbox",
    "magi_agent.execution_authority.sandbox.linux_bwrap",
    "magi_agent.execution_authority.sandbox.macos_seatbelt",
    "magi_agent.execution_authority.adapters.tool_manifest",
    "magi_agent.execution_authority.user_decision",
    "magi_agent.execution_authority.workspace_writer",
):
    importlib.import_module(name)

forbidden_prefixes = (
    "aiohttp",
    "anthropic",
    "google.adk",
    "httpx",
    "kubernetes",
    "openai",
    "psycopg",
    "redis",
    "requests",
    "socket",
    "stripe",
    "subprocess",
    "supabase",
    "telegram",
    "urllib.request",
    "urllib3",
    "magi_agent.adk_bridge",
    "magi_agent.browser",
    "magi_agent.channels",
    "magi_agent.cli",
    "magi_agent.customize",
    "magi_agent.egress_proxy",
    "magi_agent.engine",
    "magi_agent.gateway",
    "magi_agent.gates",
    "magi_agent.models",
    "magi_agent.permissions",
    "magi_agent.plugins",
    "magi_agent.providers",
    "magi_agent.routing",
    "magi_agent.runtime",
    "magi_agent.sandbox",
    "magi_agent.tools",
    "magi_agent.transport",
    "magi_agent.web_acquisition",
    "magi_agent.web_dashboard",
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
    raise AssertionError(f"execution-authority import loaded forbidden modules: {loaded}")
"""
    )

    assert completed.returncode == 0, completed.stderr


def test_execution_authority_contracts_inherit_closed_pydantic_escape_hatches() -> None:
    from magi_agent.execution_authority.contracts import ProofObligation

    obligation = ProofObligation(evidenceKinds=("test_result",), freshness="current")

    with pytest.raises(ValueError, match="model_construct is disabled"):
        ProofObligation.model_construct(
            evidenceKinds=("test_result",),
            freshness="current",
        )
    with pytest.raises(ValueError, match="model_copy update is disabled"):
        obligation.model_copy(update={"freshness": "stale"})
    with pytest.raises(ValueError, match="copy update is disabled"):
        obligation.copy(update={"freshness": "stale"})
