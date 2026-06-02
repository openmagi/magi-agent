from __future__ import annotations

import pytest

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, RegisteredHook
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.recipes.hook_composition import _STAGE_ORDER
from magi_agent.tools.manifest import ToolSource


def _context() -> HookContext:
    return HookContext(bot_id="bot-1", user_id="user-1", session_id="session-1", turn_id="turn-1")


def _manifest(name: str, *, point: HookPoint) -> HookManifest:
    return HookManifest(
        name=name,
        point=point,
        description=f"{name} hook",
        source=ToolSource(kind="builtin", package="test"),
        priority=0,
    )


def test_prompt_transform_points_are_distinct_enum_members() -> None:
    assert HookPoint.BEFORE_SYSTEM_PROMPT.value == "beforeSystemPrompt"
    assert HookPoint.BEFORE_MESSAGE_SEND.value == "beforeMessageSend"

    # Distinct from BEFORE_LLM_CALL — message modification point stays separate.
    assert HookPoint.BEFORE_SYSTEM_PROMPT is not HookPoint.BEFORE_LLM_CALL
    assert HookPoint.BEFORE_MESSAGE_SEND is not HookPoint.BEFORE_LLM_CALL
    assert HookPoint.BEFORE_SYSTEM_PROMPT is not HookPoint.BEFORE_MESSAGE_SEND

    values = {member.value for member in HookPoint}
    assert {"beforeSystemPrompt", "beforeMessageSend"}.issubset(values)


def test_prompt_transform_points_order_before_llm_call() -> None:
    system_prompt_rank = _STAGE_ORDER[HookPoint.BEFORE_SYSTEM_PROMPT.value]
    message_send_rank = _STAGE_ORDER[HookPoint.BEFORE_MESSAGE_SEND.value]
    llm_call_rank = _STAGE_ORDER[HookPoint.BEFORE_LLM_CALL.value]
    turn_start_rank = _STAGE_ORDER[HookPoint.BEFORE_TURN_START.value]

    assert turn_start_rank < system_prompt_rank < message_send_rank < llm_call_rank


@pytest.mark.parametrize(
    "point",
    (HookPoint.BEFORE_SYSTEM_PROMPT, HookPoint.BEFORE_MESSAGE_SEND),
)
def test_replace_result_flows_through_hook_bus_for_prompt_transform_points(
    point: HookPoint,
) -> None:
    replacement_payload = ["replaced section a", "replaced section b"]
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=_manifest("promptTransform", point=point),
                handler=lambda _: HookResult(action="replace", value=replacement_payload),
            ),
        )
    )

    result = bus.run(
        point=point,
        context=_context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert result.final_action == "replace"
    assert result.results[-1].action == "replace"
    assert result.results[-1].value == replacement_payload
    assert result.observation.blocked_by == ()


@pytest.mark.parametrize(
    "point",
    (HookPoint.BEFORE_SYSTEM_PROMPT, HookPoint.BEFORE_MESSAGE_SEND),
)
def test_replace_result_flows_through_hook_bus_async_for_prompt_transform_points(
    point: HookPoint,
) -> None:
    import asyncio

    replacement_payload = [{"role": "user", "content": "rewritten"}]

    async def _handler(_: HookContext) -> HookResult:
        await asyncio.sleep(0)
        return HookResult(action="replace", value=replacement_payload)

    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=_manifest("promptTransformAsync", point=point),
                handler=_handler,
            ),
        )
    )

    result = asyncio.run(
        bus.run_async(
            point=point,
            context=_context(),
            harness_state=build_default_resolved_harness_state(),
        )
    )

    assert result.final_action == "replace"
    assert result.results[-1].value == replacement_payload
