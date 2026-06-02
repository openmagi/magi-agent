from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openmagi_core_agent.harness.resolved import ResolvedHarnessPresetState
    from openmagi_core_agent.hooks.bus import HookBusRunResult
    from openmagi_core_agent.hooks.context import HookContext
    from openmagi_core_agent.hooks.manifest import HookPoint


_ADK_CALLBACK_HOOK_POINT_ATTRS: dict[str, str] = {
    "before_agent_callback": "BEFORE_TURN_START",
    "after_agent_callback": "AFTER_TURN_END",
    "before_model_callback": "BEFORE_LLM_CALL",
    "after_model_callback": "AFTER_LLM_CALL",
    "on_model_error_callback": "ON_ERROR",
    "before_tool_callback": "BEFORE_TOOL_USE",
    "after_tool_callback": "AFTER_TOOL_USE",
    "on_tool_error_callback": "ON_ERROR",
}


class _LazyHookPointMapping(Mapping[str, "HookPoint"]):
    def __getitem__(self, key: str) -> "HookPoint":
        from openmagi_core_agent.hooks.manifest import HookPoint

        return getattr(HookPoint, _ADK_CALLBACK_HOOK_POINT_ATTRS[key])

    def __iter__(self) -> Iterator[str]:
        return iter(_ADK_CALLBACK_HOOK_POINT_ATTRS)

    def __len__(self) -> int:
        return len(_ADK_CALLBACK_HOOK_POINT_ATTRS)


ADK_CALLBACK_HOOK_POINTS: Mapping[str, "HookPoint"] = _LazyHookPointMapping()


class HookBusProtocol(Protocol):
    async def run_async(
        self,
        *,
        point: "HookPoint",
        context: "HookContext",
        harness_state: "ResolvedHarnessPresetState",
    ) -> "HookBusRunResult": ...


HookContextFactory = Callable[["AdkCallbackInvocation"], "HookContext"]
AdkCallback = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class AdkCallbackInvocation:
    callback_name: str
    hook_point: "HookPoint"
    callback_context: object | None = None
    tool_context: object | None = None
    adk_context: object | None = None
    model_request: object | None = None
    model_response: object | None = None
    tool: object | None = None
    tool_args: Mapping[str, Any] | None = None
    tool_result: object | None = None
    error: BaseException | None = None
    raw_args: tuple[object, ...] = ()
    raw_kwargs: Mapping[str, Any] | None = None


class OpenMagiAdkCallbackBlocked(RuntimeError):
    def __init__(
        self,
        *,
        callback_name: str,
        hook_point: "HookPoint",
        run_result: "HookBusRunResult",
    ) -> None:
        super().__init__(
            "OpenMagi HookBus returned "
            f"{run_result.final_action!r} for ADK {callback_name} at {hook_point.value}; "
            "callback adapter fails closed until payload projection is implemented."
        )
        self.callback_name = callback_name
        self.hook_point = hook_point
        self.run_result = run_result


@dataclass(frozen=True)
class AdkCallbackAdapter:
    callbacks: dict[str, AdkCallback]
    mapping: dict[str, HookPoint]


def build_adk_callback_adapter(
    *,
    hook_bus: HookBusProtocol,
    hook_context_factory: HookContextFactory,
    harness_state: "ResolvedHarnessPresetState",
) -> AdkCallbackAdapter:
    from openmagi_core_agent.hooks.manifest import HookPoint

    async def run_invocation(invocation: AdkCallbackInvocation) -> None:
        context = hook_context_factory(invocation)
        result = await hook_bus.run_async(
            point=invocation.hook_point,
            context=context,
            harness_state=harness_state,
        )
        if result.final_action == "continue":
            return None
        raise OpenMagiAdkCallbackBlocked(
            callback_name=invocation.callback_name,
            hook_point=invocation.hook_point,
            run_result=result,
        )

    async def before_agent_callback(*, callback_context: object) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="before_agent_callback",
                hook_point=HookPoint.BEFORE_TURN_START,
                callback_context=callback_context,
                adk_context=callback_context,
                raw_kwargs={"callback_context": callback_context},
            )
        )

    async def after_agent_callback(*, callback_context: object) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="after_agent_callback",
                hook_point=HookPoint.AFTER_TURN_END,
                callback_context=callback_context,
                adk_context=callback_context,
                raw_kwargs={"callback_context": callback_context},
            )
        )

    async def before_model_callback(
        *,
        callback_context: object,
        llm_request: object,
    ) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="before_model_callback",
                hook_point=HookPoint.BEFORE_LLM_CALL,
                callback_context=callback_context,
                adk_context=callback_context,
                model_request=llm_request,
                raw_kwargs={
                    "callback_context": callback_context,
                    "llm_request": llm_request,
                },
            )
        )

    async def after_model_callback(
        *,
        callback_context: object,
        llm_response: object,
    ) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="after_model_callback",
                hook_point=HookPoint.AFTER_LLM_CALL,
                callback_context=callback_context,
                adk_context=callback_context,
                model_response=llm_response,
                raw_kwargs={
                    "callback_context": callback_context,
                    "llm_response": llm_response,
                },
            )
        )

    async def on_model_error_callback(
        *,
        callback_context: object,
        llm_request: object,
        error: BaseException,
    ) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="on_model_error_callback",
                hook_point=HookPoint.ON_ERROR,
                callback_context=callback_context,
                adk_context=callback_context,
                model_request=llm_request,
                error=error,
                raw_kwargs={
                    "callback_context": callback_context,
                    "llm_request": llm_request,
                    "error": error,
                },
            )
        )

    async def before_tool_callback(
        *,
        tool: object,
        args: dict[str, Any],
        tool_context: object,
    ) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="before_tool_callback",
                hook_point=HookPoint.BEFORE_TOOL_USE,
                tool_context=tool_context,
                adk_context=tool_context,
                tool=tool,
                tool_args=args,
                raw_kwargs={
                    "tool": tool,
                    "args": args,
                    "tool_context": tool_context,
                },
            )
        )

    async def after_tool_callback(
        *,
        tool: object,
        args: dict[str, Any],
        tool_context: object,
        tool_response: object,
    ) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="after_tool_callback",
                hook_point=HookPoint.AFTER_TOOL_USE,
                tool_context=tool_context,
                adk_context=tool_context,
                tool=tool,
                tool_args=args,
                tool_result=tool_response,
                raw_kwargs={
                    "tool": tool,
                    "args": args,
                    "tool_context": tool_context,
                    "tool_response": tool_response,
                },
            )
        )

    async def on_tool_error_callback(
        *,
        tool: object,
        args: dict[str, Any],
        tool_context: object,
        error: BaseException,
    ) -> None:
        await run_invocation(
            AdkCallbackInvocation(
                callback_name="on_tool_error_callback",
                hook_point=HookPoint.ON_ERROR,
                tool_context=tool_context,
                adk_context=tool_context,
                tool=tool,
                tool_args=args,
                error=error,
                raw_kwargs={
                    "tool": tool,
                    "args": args,
                    "tool_context": tool_context,
                    "error": error,
                },
            )
        )

    callbacks: dict[str, AdkCallback] = {
        "before_agent_callback": before_agent_callback,
        "after_agent_callback": after_agent_callback,
        "before_model_callback": before_model_callback,
        "after_model_callback": after_model_callback,
        "on_model_error_callback": on_model_error_callback,
        "before_tool_callback": before_tool_callback,
        "after_tool_callback": after_tool_callback,
        "on_tool_error_callback": on_tool_error_callback,
    }
    return AdkCallbackAdapter(
        callbacks=callbacks,
        mapping=dict(ADK_CALLBACK_HOOK_POINTS),
    )


__all__ = [
    "ADK_CALLBACK_HOOK_POINTS",
    "AdkCallbackAdapter",
    "AdkCallbackInvocation",
    "OpenMagiAdkCallbackBlocked",
    "build_adk_callback_adapter",
]
