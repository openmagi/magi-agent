from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import ExecutionType, HookManifest
from magi_agent.hooks.result import HookResult

__all__ = ["HookExecutor", "ExecutionType", "get_executor", "CommandHookExecutor", "HttpHookExecutor", "LLMHookExecutor"]


@runtime_checkable
class HookExecutor(Protocol):
    async def execute(self, context: HookContext, manifest: HookManifest) -> HookResult: ...


# Registry mapping execution_type → HookExecutor.
# Populated by PR 2 (CommandHookExecutor) and PR 3 (HttpHookExecutor).
_REGISTRY: dict[ExecutionType, HookExecutor] = {}


def get_executor(execution_type: str) -> HookExecutor | None:
    """Return the registered executor for *execution_type*, or None if unregistered."""
    return _REGISTRY.get(execution_type)


# Trigger executor self-registration.  Imports must come *after* _REGISTRY and
# get_executor are defined so the modules can call _REGISTRY[...] = ...
from magi_agent.hooks.executors.command_executor import CommandHookExecutor  # noqa: E402,F401
from magi_agent.hooks.executors.http_executor import HttpHookExecutor  # noqa: E402,F401
from magi_agent.hooks.executors.llm_executor import LLMHookExecutor  # noqa: E402,F401
