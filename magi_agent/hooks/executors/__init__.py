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


_LAZY_REGISTERED = False


def _ensure_executors_registered() -> None:
    """Trigger executor self-registration lazily on first ``get_executor`` call.

    Eager top-level imports here would pull ``httpx`` (HttpHookExecutor) and the
    LLM SDK (LLMHookExecutor) into ``sys.modules`` the moment anything imports
    ``magi_agent.hooks.bus`` — and ``httpx`` transitively imports ``rich``, which
    violates the headless cold-start import-purity gate
    (``magi_agent/cli/tests/test_coldstart.py``). Deferring registration to first
    use keeps the cold path import-clean: ``build_user_hook_bus`` only runs when
    the user-hooks flag is on, so this lazy seam fires exactly when the executors
    are needed.
    """

    global _LAZY_REGISTERED
    if _LAZY_REGISTERED:
        return
    _LAZY_REGISTERED = True
    # Imports must come *after* _REGISTRY is defined so the modules can call
    # ``_REGISTRY[...] = ...`` at import time.
    from magi_agent.hooks.executors import command_executor  # noqa: F401, PLC0415
    from magi_agent.hooks.executors import http_executor  # noqa: F401, PLC0415
    from magi_agent.hooks.executors import llm_executor  # noqa: F401, PLC0415


def get_executor(execution_type: str) -> HookExecutor | None:
    """Return the registered executor for *execution_type*, or None if unregistered."""

    _ensure_executors_registered()
    return _REGISTRY.get(execution_type)


def __getattr__(name: str):
    """Lazy re-export of the concrete executor classes.

    Keeps ``from magi_agent.hooks.executors import CommandHookExecutor`` working
    without pulling the registration imports at module-load time. PEP 562 module
    ``__getattr__`` fires only when the name is actually accessed.
    """

    if name in {"CommandHookExecutor", "HttpHookExecutor", "LLMHookExecutor"}:
        _ensure_executors_registered()
        from magi_agent.hooks.executors.command_executor import (  # noqa: PLC0415
            CommandHookExecutor,
        )
        from magi_agent.hooks.executors.http_executor import (  # noqa: PLC0415
            HttpHookExecutor,
        )
        from magi_agent.hooks.executors.llm_executor import (  # noqa: PLC0415
            LLMHookExecutor,
        )

        return {
            "CommandHookExecutor": CommandHookExecutor,
            "HttpHookExecutor": HttpHookExecutor,
            "LLMHookExecutor": LLMHookExecutor,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
