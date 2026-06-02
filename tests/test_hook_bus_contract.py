import asyncio

import pytest
from pydantic import ValidationError

from openmagi_core_agent.harness.resolved import build_default_resolved_harness_state
from openmagi_core_agent.hooks.bus import (
    HookBus,
    HookBusObservation,
    HookObserverTelemetry,
    RegisteredHook,
)
from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.manifest import HookManifest, HookPoint
from openmagi_core_agent.hooks.result import HookResult
from openmagi_core_agent.hooks.scope import HookScope
from openmagi_core_agent.tools.manifest import ToolSource


def manifest(
    name: str,
    *,
    priority: int,
    scope: HookScope | None = None,
    fail_open: bool = False,
    blocking: bool = True,
    security_critical: bool = False,
    enabled: bool = True,
) -> HookManifest:
    return HookManifest(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description=f"{name} hook",
        source=ToolSource(kind="builtin", package="test"),
        priority=priority,
        fail_open=fail_open,
        blocking=blocking,
        scope=scope or HookScope(),
        security_critical=security_critical,
        enabled=enabled,
    )


def context() -> HookContext:
    return HookContext(bot_id="bot-1", user_id="user-1", session_id="session-1", turn_id="turn-1")


def test_hook_bus_orders_by_priority_and_records_effective_hooks() -> None:
    calls: list[str] = []
    bus = HookBus(
        hooks=(
            RegisteredHook(manifest=manifest("later", priority=20), handler=lambda _: calls.append("later")),
            RegisteredHook(manifest=manifest("earlier", priority=10), handler=lambda _: calls.append("earlier")),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert calls == ["earlier", "later"]
    assert result.final_action == "continue"
    assert result.observation == HookBusObservation(
        effective_hooks=("earlier", "later"),
        skipped_by_scope=(),
        failed_open=(),
        failed_closed=(),
        blocked_by=(),
    )


def test_hook_bus_filters_scope_before_execution_and_keeps_skipped_observable() -> None:
    calls: list[str] = []
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("codingOnly", priority=10, scope=HookScope(agent_roles=("coding",))),
                handler=lambda _: calls.append("codingOnly"),
            ),
            RegisteredHook(
                manifest=manifest("researchOnly", priority=20, scope=HookScope(agent_roles=("research",))),
                handler=lambda _: calls.append("researchOnly"),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(agent_role="research", spawn_depth=1),
    )

    assert calls == ["researchOnly"]
    assert result.observation.effective_hooks == ("researchOnly",)
    assert result.observation.skipped_by_scope == ("codingOnly",)


def test_hook_bus_scope_filter_does_not_execute_duplicate_name_from_other_scope() -> None:
    calls: list[str] = []
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("duplicatePolicy", priority=10, scope=HookScope(agent_roles=("coding",))),
                handler=lambda _: calls.append("coding"),
            ),
            RegisteredHook(
                manifest=manifest("duplicatePolicy", priority=20, scope=HookScope(agent_roles=("research",))),
                handler=lambda _: calls.append("research"),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(agent_role="research", spawn_depth=1),
    )

    assert calls == ["research"]
    assert result.observation.effective_hooks == ("duplicatePolicy",)
    assert result.observation.skipped_by_scope == ("duplicatePolicy",)


def test_hook_bus_treats_security_critical_as_always_apply() -> None:
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest(
                    "sealedFiles",
                    priority=0,
                    scope=HookScope(run_on=("main",), agent_roles=("general",), max_spawn_depth=0),
                    security_critical=True,
                ),
                handler=lambda _: HookResult(action="continue"),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(agent_role="research", spawn_depth=4),
    )

    assert result.observation.effective_hooks == ("sealedFiles",)
    assert result.observation.skipped_by_scope == ()


def test_hook_bus_fail_open_continues_and_fail_closed_blocks() -> None:
    fail_open_bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("softFailure", priority=10, fail_open=True),
                handler=lambda _: (_ for _ in ()).throw(RuntimeError("soft")),
            ),
        )
    )

    soft_result = fail_open_bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert soft_result.final_action == "continue"
    assert soft_result.observation.failed_open == ("softFailure",)

    fail_closed_bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("hardFailure", priority=10, fail_open=False),
                handler=lambda _: (_ for _ in ()).throw(RuntimeError("hard")),
            ),
        )
    )

    hard_result = fail_closed_bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert hard_result.final_action == "block"
    assert hard_result.observation.failed_closed == ("hardFailure",)
    assert hard_result.results[-1].reason == "hardFailure failed closed"


def test_non_blocking_hook_returning_block_does_not_block_phase() -> None:
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("observerBlock", priority=10, blocking=False),
                handler=lambda _: HookResult(action="block", reason="observe only"),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert result.final_action == "continue"
    assert result.observation.blocked_by == ()
    assert result.results[0].action == "block"


def test_non_blocking_hook_exception_does_not_fail_closed() -> None:
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("observerFailure", priority=10, blocking=False, fail_open=False),
                handler=lambda _: (_ for _ in ()).throw(RuntimeError("observer")),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert result.final_action == "continue"
    assert result.observation.failed_closed == ()
    assert result.observation.failed_open == ("observerFailure",)


def test_permission_deny_projects_to_block_boundary_not_continue() -> None:
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("permissionGate", priority=0),
                handler=lambda _: HookResult(
                    action="permission_decision",
                    decision="deny",
                    reason="dangerous command",
                ),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert result.final_action == "block"
    assert result.permission_boundary is not None
    assert result.permission_boundary.decision == "deny"
    assert result.permission_boundary.owner == "OpenMagi ControlRequest"
    assert result.permission_boundary.requires_control_request is False
    assert result.observation.blocked_by == ("permissionGate",)


@pytest.mark.parametrize("decision", ("deny", "ask", "approve"))
def test_permission_decision_requires_permission_decision_action(decision: str) -> None:
    with pytest.raises(ValidationError, match="permission decisions require action"):
        HookResult(decision=decision)


def test_permission_decision_action_requires_concrete_decision() -> None:
    with pytest.raises(ValidationError, match="permission_decision requires a decision"):
        HookResult(action="permission_decision")


def test_permission_ask_requires_openmagi_control_request_boundary() -> None:
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest("permissionGate", priority=0),
                handler=lambda _: HookResult(
                    action="permission_decision",
                    decision="ask",
                    reason="needs user approval",
                ),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(),
    )

    assert result.final_action == "pending_control_request"
    assert result.permission_boundary is not None
    assert result.permission_boundary.decision == "ask"
    assert result.permission_boundary.owner == "OpenMagi ControlRequest"
    assert result.permission_boundary.requires_control_request is True


@pytest.mark.parametrize(
    ("hook_name", "scope", "security_critical"),
    (
        ("disabledSecurityCritical", HookScope(), True),
        ("disabledHardSafety", HookScope(hard_safety=True), False),
    ),
)
def test_disabled_protected_hook_inputs_are_included_for_scope_resolution(
    hook_name: str,
    scope: HookScope,
    security_critical: bool,
) -> None:
    calls: list[str] = []
    bus = HookBus(
        hooks=(
            RegisteredHook(
                manifest=manifest(
                    hook_name,
                    priority=0,
                    scope=scope,
                    security_critical=security_critical,
                    enabled=False,
                ),
                handler=lambda _: calls.append(hook_name),
            ),
        )
    )

    result = bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=context(),
        harness_state=build_default_resolved_harness_state(agent_role="research", spawn_depth=4),
    )

    assert calls == [hook_name]
    assert result.final_action == "continue"
    assert result.observation.effective_hooks == (hook_name,)
    assert result.observation.skipped_by_scope == ()


def test_run_async_awaits_blocking_async_and_sync_hooks() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def async_gate(_: HookContext) -> HookResult:
            calls.append("asyncGate")
            await asyncio.sleep(0)
            return HookResult(action="continue")

        def sync_gate(_: HookContext) -> HookResult:
            calls.append("syncGate")
            return HookResult(
                action="permission_decision",
                decision="ask",
                reason="needs approval",
            )

        bus = HookBus(
            hooks=(
                RegisteredHook(manifest=manifest("asyncGate", priority=0), handler=async_gate),
                RegisteredHook(manifest=manifest("syncGate", priority=10), handler=sync_gate),
            )
        )

        result = await bus.run_async(
            point=HookPoint.BEFORE_TOOL_USE,
            context=context(),
            harness_state=build_default_resolved_harness_state(),
        )

        assert calls == ["asyncGate", "syncGate"]
        assert result.final_action == "pending_control_request"
        assert result.permission_boundary is not None
        assert result.permission_boundary.source_hook == "syncGate"
        assert result.permission_boundary.requires_control_request is True

    asyncio.run(scenario())


def test_run_async_non_blocking_async_hook_is_fire_and_forget() -> None:
    async def scenario() -> None:
        observer_started = asyncio.Event()
        release_observer = asyncio.Event()

        async def observer(_: HookContext) -> HookResult:
            observer_started.set()
            await release_observer.wait()
            return HookResult(action="block", reason="observer cannot block")

        def blocking_gate(_: HookContext) -> HookResult:
            return HookResult(action="continue")

        bus = HookBus(
            hooks=(
                RegisteredHook(
                    manifest=manifest("observer", priority=0, blocking=False),
                    handler=observer,
                ),
                RegisteredHook(manifest=manifest("blockingGate", priority=10), handler=blocking_gate),
            )
        )

        result = await asyncio.wait_for(
            bus.run_async(
                point=HookPoint.BEFORE_TOOL_USE,
                context=context(),
                harness_state=build_default_resolved_harness_state(),
            ),
            timeout=0.05,
        )

        assert result.final_action == "continue"
        assert result.observation.blocked_by == ()
        assert result.observation.effective_hooks == ("observer", "blockingGate")

        await asyncio.wait_for(observer_started.wait(), timeout=0.2)
        release_observer.set()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_run_async_scope_filter_does_not_schedule_duplicate_name_from_other_scope() -> None:
    async def scenario() -> None:
        out_of_scope_started = asyncio.Event()
        calls: list[str] = []

        async def out_of_scope_observer(_: HookContext) -> HookResult:
            out_of_scope_started.set()
            return HookResult(action="continue")

        async def selected_observer(_: HookContext) -> HookResult:
            calls.append("selected")
            return HookResult(action="continue")

        bus = HookBus(
            hooks=(
                RegisteredHook(
                    manifest=manifest(
                        "duplicateObserver",
                        priority=0,
                        scope=HookScope(agent_roles=("coding",)),
                        blocking=False,
                    ),
                    handler=out_of_scope_observer,
                ),
                RegisteredHook(
                    manifest=manifest(
                        "duplicateObserver",
                        priority=10,
                        scope=HookScope(agent_roles=("research",)),
                        blocking=False,
                    ),
                    handler=selected_observer,
                ),
            )
        )

        result = await bus.run_async(
            point=HookPoint.BEFORE_TOOL_USE,
            context=context(),
            harness_state=build_default_resolved_harness_state(agent_role="research", spawn_depth=1),
        )

        await asyncio.sleep(0)

        assert calls == ["selected"]
        assert out_of_scope_started.is_set() is False
        assert result.observation.effective_hooks == ("duplicateObserver",)
        assert result.observation.skipped_by_scope == ("duplicateObserver",)

    asyncio.run(scenario())


def test_run_async_non_blocking_exceptions_are_consumed() -> None:
    async def scenario() -> None:
        observer_started = asyncio.Event()
        unhandled: list[object] = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: unhandled.append(context))

        async def observer(_: HookContext) -> HookResult:
            observer_started.set()
            raise RuntimeError("observer failure")

        try:
            bus = HookBus(
                hooks=(
                    RegisteredHook(
                        manifest=manifest(
                            "observerFailure",
                            priority=0,
                            blocking=False,
                            fail_open=False,
                        ),
                        handler=observer,
                    ),
                )
            )

            result = await bus.run_async(
                point=HookPoint.BEFORE_TOOL_USE,
                context=context(),
                harness_state=build_default_resolved_harness_state(),
            )

            await asyncio.wait_for(observer_started.wait(), timeout=0.2)
            await asyncio.sleep(0)

            assert result.final_action == "continue"
            assert result.observation.failed_closed == ()
            assert unhandled == []
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_run_async_non_blocking_observer_failure_records_failed_open_telemetry() -> None:
    async def scenario() -> None:
        observer_started = asyncio.Event()
        release_observer = asyncio.Event()
        sink_events: list[HookObserverTelemetry] = []

        async def observer(_: HookContext) -> HookResult:
            observer_started.set()
            await release_observer.wait()
            raise ValueError("observer exploded")

        bus = HookBus(
            hooks=(
                RegisteredHook(
                    manifest=manifest(
                        "observerTelemetry",
                        priority=0,
                        blocking=False,
                        fail_open=False,
                    ),
                    handler=observer,
                ),
            ),
            observer_telemetry_sink=sink_events.append,
        )

        result = await bus.run_async(
            point=HookPoint.BEFORE_TOOL_USE,
            context=context(),
            harness_state=build_default_resolved_harness_state(),
        )

        await asyncio.wait_for(observer_started.wait(), timeout=0.2)
        assert result.final_action == "continue"
        assert result.observation.failed_closed == ()
        assert result.observation.blocked_by == ()
        assert bus.drain_observer_telemetry() == ()

        release_observer.set()
        drained: tuple[HookObserverTelemetry, ...] = ()
        for _ in range(20):
            await asyncio.sleep(0.01)
            drained = bus.drain_observer_telemetry()
            if drained:
                break

        assert drained == (
            HookObserverTelemetry(
                source_hook="observerTelemetry",
                point=HookPoint.BEFORE_TOOL_USE,
                status="failed_open",
                error_type="ValueError",
                error_message="observer exploded",
            ),
        )
        assert sink_events == list(drained)
        assert bus.drain_observer_telemetry() == ()

    asyncio.run(scenario())


def test_run_async_observer_telemetry_sink_failure_is_observational() -> None:
    async def scenario() -> None:
        observer_started = asyncio.Event()
        unhandled: list[object] = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: unhandled.append(context))

        async def observer(_: HookContext) -> HookResult:
            observer_started.set()
            raise RuntimeError("observer failure")

        def failing_sink(_: HookObserverTelemetry) -> None:
            raise RuntimeError("sink failure")

        try:
            bus = HookBus(
                hooks=(
                    RegisteredHook(
                        manifest=manifest(
                            "observerTelemetry",
                            priority=0,
                            blocking=False,
                            fail_open=False,
                        ),
                        handler=observer,
                    ),
                ),
                observer_telemetry_sink=failing_sink,
            )

            result = await bus.run_async(
                point=HookPoint.BEFORE_TOOL_USE,
                context=context(),
                harness_state=build_default_resolved_harness_state(),
            )

            await asyncio.wait_for(observer_started.wait(), timeout=0.2)
            drained: tuple[HookObserverTelemetry, ...] = ()
            for _ in range(20):
                await asyncio.sleep(0.01)
                drained = bus.drain_observer_telemetry()
                if drained:
                    break

            assert result.final_action == "continue"
            assert result.observation.failed_closed == ()
            assert result.observation.blocked_by == ()
            assert drained[0].source_hook == "observerTelemetry"
            assert drained[0].status == "failed_open"
            assert unhandled == []
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())
