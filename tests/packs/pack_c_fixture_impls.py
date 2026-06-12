"""Importable provider impls for the C0 end-to-end loader test.

These are deliberately external-shaped: each receives ONLY its typed
provide-context — identical capability to a ~/.magi/packs pack (§1).
"""
from __future__ import annotations

from typing import Any


def provide_loop_policy(context: Any) -> None:
    context.register("loop_policy:fake@1", lambda loop_input: loop_input)


def provide_schedule_policy(context: Any) -> None:
    context.register("schedule_policy:fake@1", object())


def provide_memory_strategy(context: Any) -> None:
    context.register("memory_strategy:fake@1", object())


def provide_workspace_handler(context: Any) -> None:
    if context.register_workspace_handler is not None:
        context.register_workspace_handler(
            "FakeTool", lambda args, view: {"echo": dict(args)}
        )


def _ralph_override(loop_input: Any) -> Any:
    raise AssertionError("override marker — never executed in this test")


def provide_ralph_override(context: Any) -> None:
    context.register("loop_policy:ralph@1", _ralph_override)


class _CronOverridePolicy:
    """Override marker satisfying the SchedulePolicy protocol shape;
    never ticked in the override test."""

    def select_due(self, due: Any, *, now: Any) -> Any:
        raise AssertionError("override marker — never executed in this test")

    def next_run_after_fire(self, job: Any, *, now: Any) -> Any:
        raise AssertionError("override marker — never executed in this test")


def provide_cron_override(context: Any) -> None:
    context.register("schedule_policy:cron@1", _CronOverridePolicy())
