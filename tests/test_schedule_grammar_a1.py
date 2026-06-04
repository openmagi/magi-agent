"""A1 — ScheduleSpec once/interval/cron grammar (preview-only).

TDD test suite written BEFORE implementation.
Tests verify:
- parse_schedule correctly classifies once/interval/cron
- next_run_at returns correct datetimes for all three kinds
- once-in-the-past returns None
- invalid inputs raise ValueError
- timezone-awareness is correct (matches cron_policy tz conventions)
- regression: existing CronNextRunPreview / _next_fire_after still work
"""
from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _tz(name: str, year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(name))


# ---------------------------------------------------------------------------
# parse_schedule — once kind
# ---------------------------------------------------------------------------

class TestParseScheduleOnce:
    def test_once_relative_minutes(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("30m")
        assert spec.kind == "once"
        assert spec.expression == "30m"

    def test_once_relative_hours(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("2h")
        assert spec.kind == "once"
        assert spec.expression == "2h"

    def test_once_relative_seconds(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("90s")
        assert spec.kind == "once"
        assert spec.expression == "90s"

    def test_once_relative_days(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("1d")
        assert spec.kind == "once"
        assert spec.expression == "1d"

    def test_once_iso_timestamp(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("2026-06-10T12:00:00+00:00")
        assert spec.kind == "once"
        assert spec.expression == "2026-06-10T12:00:00+00:00"

    def test_once_iso_timestamp_utc_z(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("2026-06-10T12:00:00Z")
        assert spec.kind == "once"

    def test_once_model_is_frozen(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("30m")
        with pytest.raises((TypeError, Exception)):
            spec.kind = "interval"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_schedule — interval kind
# ---------------------------------------------------------------------------

class TestParseScheduleInterval:
    def test_interval_minutes(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("every 30m")
        assert spec.kind == "interval"
        assert spec.expression == "every 30m"

    def test_interval_hours(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("every 2h")
        assert spec.kind == "interval"

    def test_interval_days(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("every 1d")
        assert spec.kind == "interval"

    def test_interval_seconds(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("every 90s")
        assert spec.kind == "interval"
        assert spec.expression == "every 90s"

    def test_interval_model_is_frozen(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("every 30m")
        with pytest.raises((TypeError, Exception)):
            spec.kind = "once"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_schedule — cron kind
# ---------------------------------------------------------------------------

class TestParseScheduleCron:
    def test_cron_five_fields(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("*/15 * * * *")
        assert spec.kind == "cron"
        assert spec.expression == "*/15 * * * *"

    def test_cron_specific_time(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("0 9 * * *")
        assert spec.kind == "cron"

    def test_cron_model_is_frozen(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        spec = parse_schedule("0 9 * * *")
        with pytest.raises((TypeError, Exception)):
            spec.kind = "once"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# parse_schedule — invalid inputs
# ---------------------------------------------------------------------------

class TestParseScheduleInvalid:
    def test_empty_string_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError, match=r"schedule"):
            parse_schedule("")

    def test_whitespace_only_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("   ")

    def test_unknown_duration_unit_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("5x")

    def test_interval_missing_duration_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("every")

    def test_interval_unknown_unit_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("every 5x")

    def test_cron_wrong_field_count_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("* * * *")

    def test_cron_six_fields_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("* * * * * *")

    def test_invalid_iso_timestamp_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("not-a-date-or-duration")

    def test_cron_out_of_range_raises(self) -> None:
        from magi_agent.missions.schedule_grammar import parse_schedule

        with pytest.raises(ValueError):
            parse_schedule("60 * * * *")


# ---------------------------------------------------------------------------
# next_run_at — once kind
# ---------------------------------------------------------------------------

class TestNextRunAtOnce:
    def test_once_relative_minutes_future(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("30m")
        result = next_run_at(spec, now=now)

        assert result is not None
        expected = now + timedelta(minutes=30)
        assert result == expected

    def test_once_relative_hours_future(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("2h")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result == now + timedelta(hours=2)

    def test_once_relative_seconds_future(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("90s")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result == now + timedelta(seconds=90)

    def test_once_relative_days_future(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("1d")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result == now + timedelta(days=1)

    def test_once_iso_future_returns_that_time(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("2026-06-10T12:00:00+00:00")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result == _utc(2026, 6, 10, 12, 0)

    def test_once_iso_past_returns_none(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 10, 13, 0)  # after the target
        spec = parse_schedule("2026-06-10T12:00:00+00:00")
        result = next_run_at(spec, now=now)

        assert result is None

    def test_once_iso_exactly_now_returns_none(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 10, 12, 0)
        spec = parse_schedule("2026-06-10T12:00:00+00:00")
        result = next_run_at(spec, now=now)

        # at-or-before now → None (already passed)
        assert result is None

    def test_once_relative_always_in_future(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("1h")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result > now

    def test_once_result_is_timezone_aware(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("30m")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# next_run_at — interval kind
# ---------------------------------------------------------------------------

class TestNextRunAtInterval:
    def test_interval_no_last_fire_anchors_to_now(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 30m")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result == now + timedelta(minutes=30)

    def test_interval_with_last_fire_adds_interval(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        last_fire = _utc(2026, 6, 3, 11, 30)
        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 30m")
        result = next_run_at(spec, now=now, last_fire=last_fire)

        assert result is not None
        assert result == last_fire + timedelta(minutes=30)

    def test_interval_with_last_fire_hours(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        last_fire = _utc(2026, 6, 3, 10, 0)
        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 2h")
        result = next_run_at(spec, now=now, last_fire=last_fire)

        assert result == last_fire + timedelta(hours=2)

    def test_interval_result_is_timezone_aware(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 1d")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.tzinfo is not None

    def test_interval_always_in_future_relative_to_now_anchor(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 1h")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result > now

    def test_interval_1d_seconds(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 1d")
        result = next_run_at(spec, now=now)

        assert result == now + timedelta(days=1)


# ---------------------------------------------------------------------------
# next_run_at — cron kind
# ---------------------------------------------------------------------------

class TestNextRunAtCron:
    def test_cron_every_15m_next_run(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        # now = 2026-06-03 12:00 UTC — next should be 12:15
        now = _utc(2026, 6, 3, 12, 0)
        now_ms = int(now.timestamp() * 1000)
        spec = parse_schedule("*/15 * * * *")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.minute == 15
        assert result.hour == 12
        assert result > now

    def test_cron_daily_9am_utc(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 8, 0)
        spec = parse_schedule("0 9 * * *")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.hour == 9
        assert result.minute == 0

    def test_cron_tz_seoul(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        # now = 2026-06-03 23:00 UTC → Seoul is 2026-06-04 08:00 KST
        # cron "0 9 * * *" in Seoul timezone → next fire = 2026-06-04 09:00 KST = 00:00 UTC
        now = _utc(2026, 6, 3, 23, 0)
        spec = parse_schedule("0 9 * * *")
        result = next_run_at(spec, now=now, timezone="Asia/Seoul")

        assert result is not None
        # result should be 2026-06-04 00:00 UTC (= 09:00 Seoul)
        assert result.astimezone(UTC).hour == 0
        assert result.astimezone(UTC).day == 4

    def test_cron_result_is_timezone_aware(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("*/15 * * * *")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.tzinfo is not None

    def test_cron_last_fire_param_ignored(self) -> None:
        """last_fire has no effect for cron kind (cron is absolute schedule)."""
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("0 9 * * *")
        result_with = next_run_at(spec, now=now, last_fire=_utc(2026, 6, 2, 9, 0))
        result_without = next_run_at(spec, now=now)

        assert result_with == result_without


# ---------------------------------------------------------------------------
# Timezone-awareness correctness
# ---------------------------------------------------------------------------

class TestNextRunAtTimezone:
    def test_now_must_be_timezone_aware(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        naive_now = datetime(2026, 6, 3, 12, 0)  # no tzinfo
        spec = parse_schedule("30m")
        with pytest.raises((ValueError, TypeError)):
            next_run_at(spec, now=naive_now)

    def test_once_iso_with_offset_converts_to_utc(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        # 2026-06-10 21:00 +09:00 = 2026-06-10 12:00 UTC
        spec = parse_schedule("2026-06-10T21:00:00+09:00")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.astimezone(UTC) == _utc(2026, 6, 10, 12, 0)

    def test_interval_tz_result_utc_equivalent(self) -> None:
        from magi_agent.missions.schedule_grammar import next_run_at, parse_schedule

        now = _utc(2026, 6, 3, 12, 0)
        spec = parse_schedule("every 1h")
        result = next_run_at(spec, now=now)

        assert result is not None
        assert result.astimezone(UTC) == _utc(2026, 6, 3, 13, 0)


# ---------------------------------------------------------------------------
# Regression: existing CronNextRunPreview / _next_fire_after unchanged
# ---------------------------------------------------------------------------

class TestRegressionExistingCronPath:
    def test_cron_next_run_preview_still_works(self) -> None:
        from magi_agent.missions.cron_policy import CronNextRunPreview
        from magi_agent.missions.receipts import sha256_ref

        preview = CronNextRunPreview(
            timezone="UTC",
            nextRunAt=1_700_000_000_000,
            scheduleDigest=sha256_ref("*/15 * * * *"),
        )
        assert preview.next_run_at == 1_700_000_000_000
        assert preview.timezone == "UTC"

    def test_cron_policy_next_fire_after_still_works(self) -> None:
        from magi_agent.missions.cron_policy import _next_fire_after  # type: ignore[attr-defined]

        result = _next_fire_after(
            expression="*/15 * * * *",
            timezone="UTC",
            now=1_600_000,
        )
        assert isinstance(result, int)
        assert result > 1_600_000

    def test_evaluate_cron_mutation_still_works(self) -> None:
        from magi_agent.missions.cron_policy import (
            CronMutationPolicy,
            CronMutationRequest,
            CronSchedulerMutationConfig,
            evaluate_cron_mutation,
        )

        request = CronMutationRequest(
            requestId="regression-req",
            missionId="mission:reg",
            runId="run:reg",
            turnId="turn:reg",
            operation="create",
            cronId="cron:reg",
            scheduleExpression="*/15 * * * *",
            timezone="UTC",
            now=1_600_000,
            idempotencyKey="idempotency:reg",
            approvalRef="approval:reg",
            evidenceRefs=("evidence:reg",),
            compensationPolicy="manual_review_required",
        )
        policy = CronMutationPolicy(
            policyRef="policy:reg",
            policySnapshotRef="policy-snapshot:reg",
            localFakeMutationAllowed=True,
            allowedTimezones=("UTC",),
        )
        config = CronSchedulerMutationConfig(
            enabled=True,
            localFakeSchedulerReceiptsEnabled=True,
        )
        result = evaluate_cron_mutation(config=config, request=request, policy=policy)
        assert result.status == "recorded_local_fake"

    def test_no_new_runtime_imports_from_schedule_grammar(self) -> None:
        """schedule_grammar must not import live-runtime or agent-execution modules.

        Uses the same forbidden-prefix list as the existing cron_policy boundary
        test (test_cron_scheduler_mutation_boundary.py) so parity is maintained.
        Note: urllib/socket/subprocess are pulled in by pydantic transitively even
        from cron_policy itself and are excluded from this check for the same reason
        the existing boundary test excludes them.
        """
        import subprocess
        import sys

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import importlib, sys
importlib.import_module("magi_agent.missions.schedule_grammar")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge.runner",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "playwright",
    "selenium",
)
loaded = [
    name for name in sys.modules
    if any(name == p or name.startswith(f"{p}.") for p in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
