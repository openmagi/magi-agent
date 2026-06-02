from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from typing import Literal

from pydantic import BaseModel, ConfigDict

from openmagi_core_agent.harness.resolved import (
    ResolvedHarnessPresetState,
    resolve_scoped_harness_hooks,
)
from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.executors import HookExecutor, get_executor
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint
from openmagi_core_agent.hooks.result import HookResult
from openmagi_core_agent.telemetry.trace_context import get_trace

logger = logging.getLogger(__name__)


HookHandlerReturn = HookResult | None | Awaitable[HookResult | None]
HookHandler = Callable[[HookContext], HookHandlerReturn]
HookObserverTelemetrySink = Callable[["HookObserverTelemetry"], None]


@dataclass(frozen=True)
class RegisteredHook:
    manifest: HookManifest
    handler: HookHandler


class HookBusObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    effective_hooks: tuple[str, ...] = ()
    skipped_by_scope: tuple[str, ...] = ()
    failed_open: tuple[str, ...] = ()
    failed_closed: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()


class HookObserverTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_hook: str
    point: HookPoint
    status: Literal["failed_open"]
    error_type: str
    error_message: str


class HookPermissionBoundary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_hook: str
    decision: Literal["approve", "deny", "ask"]
    owner: Literal["OpenMagi ControlRequest"] = "OpenMagi ControlRequest"
    requires_control_request: bool = False
    reason: str | None = None


class HookBusRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    final_action: str
    results: tuple[HookResult, ...]
    observation: HookBusObservation
    harness_state: ResolvedHarnessPresetState
    permission_boundary: HookPermissionBoundary | None = None


class HookBus:
    def __init__(
        self,
        *,
        hooks: tuple[RegisteredHook, ...] = (),
        observer_telemetry_sink: HookObserverTelemetrySink | None = None,
        command_executor: HookExecutor | None = None,
        http_executor: HookExecutor | None = None,
        llm_executor: HookExecutor | None = None,
    ) -> None:
        self._hooks = hooks
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._observer_telemetry: list[HookObserverTelemetry] = []
        self._observer_telemetry_sink = observer_telemetry_sink
        # Executors: use provided instances or auto-resolve from the registry.
        self._command_executor: HookExecutor | None = (
            command_executor if command_executor is not None else get_executor("command")
        )
        self._http_executor: HookExecutor | None = (
            http_executor if http_executor is not None else get_executor("http")
        )
        self._llm_executor: HookExecutor | None = (
            llm_executor if llm_executor is not None else get_executor("llm")
        )
        # Warn at construction time so misconfigured deployments surface early.
        if self._command_executor is None:
            logger.warning(
                "HookBus: no command executor available; command hooks will fail open"
            )
        if self._http_executor is None:
            logger.warning(
                "HookBus: no http executor available; http hooks will fail open"
            )
        if self._llm_executor is None:
            logger.warning(
                "HookBus: no llm executor available; llm hooks will fail open"
            )

    def drain_observer_telemetry(self) -> tuple[HookObserverTelemetry, ...]:
        telemetry = tuple(self._observer_telemetry)
        self._observer_telemetry.clear()
        return telemetry

    def run(
        self,
        *,
        point: HookPoint,
        context: HookContext,
        harness_state: ResolvedHarnessPresetState,
    ) -> HookBusRunResult:
        point_hooks = self._point_hooks(point)
        selected_manifests, resolved_state = resolve_scoped_harness_hooks(
            tuple(hook.manifest for hook in point_hooks),
            harness_state,
        )
        selected_manifest_ids = {id(manifest) for manifest in selected_manifests}

        results: list[HookResult] = []
        failed_open: list[str] = []
        failed_closed: list[str] = []
        blocked_by: list[str] = []
        permission_boundary: HookPermissionBoundary | None = None
        final_action = "continue"

        for hook in point_hooks:
            if id(hook.manifest) not in selected_manifest_ids:
                continue
            # External hooks (command/http) require an async context; skip them
            # here and warn.  Callers that want external hooks must use run_async().
            # The hook name is added to failed_open so operators can observe the
            # skip in the HookBusObservation rather than it being silently dropped.
            if hook.manifest.execution_type in ("command", "http", "llm"):
                logger.warning(
                    "hook '%s' (execution_type='%s') requires run_async(); "
                    "skipping in synchronous run()",
                    hook.manifest.name,
                    hook.manifest.execution_type,
                )
                failed_open.append(hook.manifest.name)
                results.append(HookResult(action="continue", reason=f"{hook.manifest.name} skipped (async required)"))
                continue
            try:
                result = _call_hook_sync(hook, context) or HookResult(action="continue")
            except Exception:
                if hook.manifest.fail_open or not hook.manifest.blocking:
                    failed_open.append(hook.manifest.name)
                    results.append(
                        HookResult(action="continue", reason=f"{hook.manifest.name} failed open")
                    )
                    continue
                failed_closed.append(hook.manifest.name)
                blocked_by.append(hook.manifest.name)
                results.append(HookResult(action="block", reason=f"{hook.manifest.name} failed closed"))
                final_action = "block"
                break

            results.append(result)
            if not hook.manifest.blocking:
                continue
            if result.action == "permission_decision":
                permission_boundary = _project_permission_boundary(hook.manifest.name, result)
                if result.decision == "deny":
                    blocked_by.append(hook.manifest.name)
                    final_action = "block"
                    break
                if result.decision == "ask":
                    final_action = "pending_control_request"
                    break
                continue
            if result.action == "block":
                blocked_by.append(hook.manifest.name)
                final_action = "block"
                break
            if result.action != "continue" and final_action == "continue":
                final_action = result.action

        trace = get_trace()
        if trace is not None:
            trace.record("hook", "HookBus", "run", f"point={point.value}, effective={len(resolved_state.effective_hooks)}, blocked_by={list(blocked_by)}")

        return HookBusRunResult(
            final_action=final_action,
            results=tuple(results),
            observation=HookBusObservation(
                effective_hooks=resolved_state.effective_hooks,
                skipped_by_scope=resolved_state.skipped_by_scope,
                failed_open=tuple(failed_open),
                failed_closed=tuple(failed_closed),
                blocked_by=tuple(blocked_by),
            ),
            harness_state=resolved_state,
            permission_boundary=permission_boundary,
        )

    async def run_async(
        self,
        *,
        point: HookPoint,
        context: HookContext,
        harness_state: ResolvedHarnessPresetState,
    ) -> HookBusRunResult:
        point_hooks = self._point_hooks(point)
        selected_manifests, resolved_state = resolve_scoped_harness_hooks(
            tuple(hook.manifest for hook in point_hooks),
            harness_state,
        )
        selected_manifest_ids = {id(manifest) for manifest in selected_manifests}

        results: list[HookResult] = []
        failed_open: list[str] = []
        failed_closed: list[str] = []
        blocked_by: list[str] = []
        permission_boundary: HookPermissionBoundary | None = None
        final_action = "continue"

        for hook in point_hooks:
            if id(hook.manifest) not in selected_manifest_ids:
                continue
            if not hook.manifest.blocking:
                # Non-blocking hooks are fire-and-forget: they run in the
                # background and their HookResult is deliberately excluded from
                # the returned results list so they cannot influence the final
                # action or permission boundary.
                self._schedule_non_blocking_hook(hook, context)
                continue
            try:
                result = await _dispatch_hook_async(
                    hook, context, self._command_executor, self._http_executor,
                    self._llm_executor,
                ) or HookResult(action="continue")
            except Exception:
                if hook.manifest.fail_open:
                    failed_open.append(hook.manifest.name)
                    results.append(
                        HookResult(action="continue", reason=f"{hook.manifest.name} failed open")
                    )
                    continue
                failed_closed.append(hook.manifest.name)
                blocked_by.append(hook.manifest.name)
                results.append(HookResult(action="block", reason=f"{hook.manifest.name} failed closed"))
                final_action = "block"
                break

            results.append(result)
            if result.action == "permission_decision":
                permission_boundary = _project_permission_boundary(hook.manifest.name, result)
                if result.decision == "deny":
                    blocked_by.append(hook.manifest.name)
                    final_action = "block"
                    break
                if result.decision == "ask":
                    final_action = "pending_control_request"
                    break
                continue
            if result.action == "block":
                blocked_by.append(hook.manifest.name)
                final_action = "block"
                break
            if result.action != "continue" and final_action == "continue":
                final_action = result.action

        trace = get_trace()
        if trace is not None:
            trace.record("hook", "HookBus", "run", f"point={point.value}, effective={len(resolved_state.effective_hooks)}, blocked_by={list(blocked_by)}")

        return HookBusRunResult(
            final_action=final_action,
            results=tuple(results),
            observation=HookBusObservation(
                effective_hooks=resolved_state.effective_hooks,
                skipped_by_scope=resolved_state.skipped_by_scope,
                failed_open=tuple(failed_open),
                failed_closed=tuple(failed_closed),
                blocked_by=tuple(blocked_by),
            ),
            harness_state=resolved_state,
            permission_boundary=permission_boundary,
        )

    def _point_hooks(self, point: HookPoint) -> tuple[RegisteredHook, ...]:
        return tuple(
            sorted(
                (
                    hook
                    for hook in self._hooks
                    if hook.manifest.point is point and _is_bus_enabled(hook.manifest)
                ),
                key=lambda hook: (hook.manifest.priority, hook.manifest.name),
            )
        )

    def _schedule_non_blocking_hook(self, hook: RegisteredHook, context: HookContext) -> None:
        task = asyncio.create_task(
            _run_non_blocking_hook(
                hook, context, self._record_observer_telemetry,
                self._command_executor, self._http_executor,
                self._llm_executor,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _record_observer_telemetry(self, telemetry: HookObserverTelemetry) -> None:
        self._observer_telemetry.append(telemetry)
        if self._observer_telemetry_sink is None:
            return
        try:
            self._observer_telemetry_sink(telemetry)
        except Exception:
            return


def _is_bus_enabled(manifest: HookManifest) -> bool:
    return manifest.enabled or manifest.security_critical or manifest.scope.hard_safety


def _project_permission_boundary(source_hook: str, result: HookResult) -> HookPermissionBoundary:
    decision = result.decision or "ask"
    return HookPermissionBoundary(
        source_hook=source_hook,
        decision=decision,
        requires_control_request=decision == "ask",
        reason=result.reason,
    )


def _call_hook_sync(hook: RegisteredHook, context: HookContext) -> HookResult | None:
    result = hook.handler(context)
    if inspect.isawaitable(result):
        raise TypeError(f"{hook.manifest.name} returned an awaitable; use HookBus.run_async")
    return result


async def _call_hook_async(hook: RegisteredHook, context: HookContext) -> HookResult | None:
    if inspect.iscoroutinefunction(hook.handler):
        result = await hook.handler(context)
    else:
        result = hook.handler(context)
        if inspect.isawaitable(result):
            result = await result
    return result


async def _dispatch_hook_async(
    hook: RegisteredHook,
    context: HookContext,
    command_executor: HookExecutor | None,
    http_executor: HookExecutor | None,
    llm_executor: HookExecutor | None = None,
) -> HookResult | None:
    """Dispatch a hook to the appropriate executor based on execution_type.

    - ``"command"`` → command_executor.execute()
    - ``"http"``    → http_executor.execute()
    - ``"llm"``     → llm_executor.execute()
    - ``"handler"`` → existing async handler call path

    If the required executor is not available (None), logs a warning and returns
    a ``continue`` result (fail-open).
    """
    execution_type = hook.manifest.execution_type

    if execution_type == "command":
        if command_executor is None:
            logger.warning(
                "hook '%s' has execution_type='command' but no command executor is registered; "
                "returning continue",
                hook.manifest.name,
            )
            return HookResult(action="continue", reason=f"{hook.manifest.name}: no command executor")
        return await command_executor.execute(context, hook.manifest)

    if execution_type == "http":
        if http_executor is None:
            logger.warning(
                "hook '%s' has execution_type='http' but no http executor is registered; "
                "returning continue",
                hook.manifest.name,
            )
            return HookResult(action="continue", reason=f"{hook.manifest.name}: no http executor")
        return await http_executor.execute(context, hook.manifest)

    if execution_type == "llm":
        if llm_executor is None:
            logger.warning(
                "hook '%s' has execution_type='llm' but no llm executor is registered; "
                "returning continue",
                hook.manifest.name,
            )
            return HookResult(action="continue", reason=f"{hook.manifest.name}: no llm executor")
        return await llm_executor.execute(context, hook.manifest)

    # Default: "handler" — use the registered async handler.
    return await _call_hook_async(hook, context)


async def _run_non_blocking_hook(
    hook: RegisteredHook,
    context: HookContext,
    record_telemetry: HookObserverTelemetrySink,
    command_executor: HookExecutor | None = None,
    http_executor: HookExecutor | None = None,
    llm_executor: HookExecutor | None = None,
) -> None:
    try:
        execution_type = hook.manifest.execution_type
        if execution_type in ("command", "http", "llm"):
            await _dispatch_hook_async(hook, context, command_executor, http_executor, llm_executor)
        elif inspect.iscoroutinefunction(hook.handler):
            await hook.handler(context)
        else:
            result = await asyncio.to_thread(hook.handler, context)
            if inspect.isawaitable(result):
                await result
    except Exception as exc:
        record_telemetry(
            HookObserverTelemetry(
                source_hook=hook.manifest.name,
                point=hook.manifest.point,
                status="failed_open",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        )
        return
