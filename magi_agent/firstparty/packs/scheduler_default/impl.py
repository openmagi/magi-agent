"""First-party schedule policy provider (no privilege, typed-ctx only)."""
from __future__ import annotations

from magi_agent.packs.context import SchedulePolicyProvideContext


def provide_cron_policy(context: SchedulePolicyProvideContext) -> None:
    from magi_agent.harness.scheduler_executor import CronSchedulePolicy

    context.register("schedule_policy:cron@1", CronSchedulePolicy())
