"""A1 — ScheduleSpec: once / interval / cron grammar (preview-only).

Pure calculation module.  No I/O, no scheduler loop, no agent spawn.

Public API
----------
parse_schedule(expr)            -> ScheduleSpec
next_run_at(spec, *, now, last_fire=None, timezone="UTC") -> datetime | None
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

# Reuse the existing 5-field cron parser from cron_policy (no reimplementation).
from magi_agent.missions.cron_policy import _next_fire_after, _parse_cron_field  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Model config — identical pattern to cron_policy._MODEL_CONFIG
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)

# ---------------------------------------------------------------------------
# Duration parsing helpers
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^(\d+)(s|m|h|d)$")
_UNIT_SECONDS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration(raw: str) -> timedelta:
    """Parse a relative duration like '30m', '2h', '90s', '1d'."""
    m = _DURATION_RE.match(raw.strip())
    if not m:
        raise ValueError(
            f"invalid duration '{raw}': expected <N>(s|m|h|d) e.g. 30m, 2h, 90s, 1d"
        )
    value = int(m.group(1))
    unit = m.group(2)
    if value <= 0:
        raise ValueError(
            f"invalid duration '{raw}': value must be > 0 (non-positive durations are not allowed)"
        )
    return timedelta(seconds=value * _UNIT_SECONDS[unit])


def _parse_iso_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp; returns a timezone-aware datetime."""
    # Handle trailing 'Z' → '+00:00' for Python <3.11 fromisoformat compat
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp '{raw}': {exc}") from exc
    if dt.tzinfo is None:
        # Naive ISO timestamps are not accepted — ambiguous timezone.
        raise ValueError(
            f"ISO-8601 timestamp '{raw}' must include timezone offset (e.g. +00:00 or Z)"
        )
    return dt


# ---------------------------------------------------------------------------
# ScheduleSpec model
# ---------------------------------------------------------------------------

ScheduleKind = Literal["once", "interval", "cron"]


class ScheduleSpec(BaseModel):
    """Immutable, parsed representation of a schedule expression."""

    model_config = _MODEL_CONFIG

    kind: ScheduleKind
    expression: str = Field()
    # For 'once' with ISO timestamp: the absolute fire time (UTC).
    fire_at: datetime | None = Field(default=None, alias="fireAt")
    # For 'once' with relative duration, or 'interval': timedelta in seconds.
    interval_seconds: int | None = Field(default=None, alias="intervalSeconds")
    # For 'cron': validated parsed fields tuple (expression string is the source).
    cron_fields_validated: bool = Field(default=False, alias="cronFieldsValidated")


# ---------------------------------------------------------------------------
# parse_schedule
# ---------------------------------------------------------------------------

def parse_schedule(expr: str) -> ScheduleSpec:
    """Classify and parse a schedule expression into a ScheduleSpec.

    Accepted forms
    --------------
    once      ::= <duration> | <iso-timestamp>
    interval  ::= "every" <duration>
    cron      ::= <field> <field> <field> <field> <field>   (5 fields)

    Raises ValueError on invalid/unrecognized input.
    """
    raw = expr.strip()
    if not raw:
        raise ValueError("schedule expression must not be empty")

    # --- interval: starts with "every " (space required) ---
    if raw.lower().startswith("every "):
        rest = raw[len("every "):].strip()
        if not rest:
            raise ValueError(
                f"invalid interval schedule '{expr}': missing duration after 'every'"
            )
        td = _parse_duration(rest)  # raises ValueError on bad unit
        return ScheduleSpec(
            kind="interval",
            expression=expr,
            intervalSeconds=int(td.total_seconds()),
        )

    # --- cron: exactly 5 whitespace-separated fields ---
    fields = raw.split()
    if len(fields) == 5:
        # Validate by running the existing parser (raises ValueError on bad input).
        _parse_cron_field(fields[0], 0, 59)
        _parse_cron_field(fields[1], 0, 23)
        _parse_cron_field(fields[2], 1, 31)
        _parse_cron_field(fields[3], 1, 12)
        _parse_cron_field(fields[4], 0, 7)
        return ScheduleSpec(
            kind="cron",
            expression=expr,
            cronFieldsValidated=True,
        )

    # Reject any multi-word expression that isn't "every <dur>" or 5-field cron.
    if len(fields) > 1:
        raise ValueError(
            f"unrecognized schedule '{expr}': "
            "expected 'every <duration>', a 5-field cron, or a single duration/ISO timestamp"
        )

    # --- once: single token — try duration first, then ISO timestamp ---
    token = fields[0]
    if _DURATION_RE.match(token):
        td = _parse_duration(token)
        return ScheduleSpec(
            kind="once",
            expression=expr,
            intervalSeconds=int(td.total_seconds()),
        )

    # Try ISO-8601 timestamp.
    fire_at = _parse_iso_timestamp(token)  # raises ValueError on bad input
    return ScheduleSpec(
        kind="once",
        expression=expr,
        fireAt=fire_at,
    )


# ---------------------------------------------------------------------------
# next_run_at
# ---------------------------------------------------------------------------

def next_run_at(
    spec: ScheduleSpec,
    *,
    now: datetime,
    last_fire: datetime | None = None,
    timezone: str = "UTC",
) -> datetime | None:
    """Compute the next fire time for *spec* given *now*.

    Parameters
    ----------
    spec:
        A parsed ScheduleSpec returned by parse_schedule().
    now:
        The current time.  MUST be timezone-aware.
    last_fire:
        For 'interval' kind: the previous fire time (timezone-aware).
        Ignored for 'once' and 'cron' kinds.
    timezone:
        IANA timezone name used for 'cron' kind computation.
        Ignored for 'once' and 'interval' kinds (they use UTC math).

    Returns
    -------
    datetime | None
        The next fire time (timezone-aware, typically UTC), or None if
        the schedule has no future run (e.g. 'once' timestamp already past).

    Raises
    ------
    ValueError
        If *now* is timezone-naive.
    TypeError
        If *now* is not a datetime.
    """
    if not isinstance(now, datetime):
        raise TypeError(f"'now' must be a datetime, got {type(now).__name__}")
    if now.tzinfo is None:
        raise ValueError("'now' must be timezone-aware (tzinfo must not be None)")

    if spec.kind == "once":
        return _next_run_once(spec, now=now)

    if spec.kind == "interval":
        return _next_run_interval(spec, now=now, last_fire=last_fire)

    # cron
    return _next_run_cron(spec, now=now, timezone=timezone)


# ---------------------------------------------------------------------------
# kind-specific helpers
# ---------------------------------------------------------------------------

def _next_run_once(spec: ScheduleSpec, *, now: datetime) -> datetime | None:
    """'once' kind: absolute fire time or now + relative offset."""
    if spec.fire_at is not None:
        # Absolute ISO timestamp: only fire if still in the future.
        fire_utc = spec.fire_at.astimezone(UTC)
        now_utc = now.astimezone(UTC)
        if fire_utc <= now_utc:
            return None
        return fire_utc

    # Relative duration: always in the future relative to now.
    if spec.interval_seconds is None:
        raise ValueError("ScheduleSpec of kind 'once' with relative duration must have interval_seconds set")
    return now.astimezone(UTC) + timedelta(seconds=spec.interval_seconds)


def _next_run_interval(
    spec: ScheduleSpec,
    *,
    now: datetime,
    last_fire: datetime | None,
) -> datetime | None:
    """'interval' kind: next fire strictly >= now, advancing whole intervals from anchor."""
    if spec.interval_seconds is None:
        raise ValueError("ScheduleSpec of kind 'interval' must have interval_seconds set")
    td = timedelta(seconds=spec.interval_seconds)
    now_utc = now.astimezone(UTC)
    if last_fire is None:
        return now_utc + td
    anchor = last_fire.astimezone(UTC)
    candidate = anchor + td
    # Advance by whole intervals until candidate is strictly >= now.
    if candidate < now_utc:
        elapsed = (now_utc - anchor).total_seconds()
        intervals_needed = int(elapsed / spec.interval_seconds)
        candidate = anchor + td * intervals_needed
        if candidate < now_utc:
            candidate += td
    return candidate


def _next_run_cron(
    spec: ScheduleSpec,
    *,
    now: datetime,
    timezone: str,
) -> datetime | None:
    """'cron' kind: delegate to the existing _next_fire_after parser."""
    try:
        next_ms = _next_fire_after(
            expression=spec.expression,
            timezone=timezone,
            now=int(now.astimezone(UTC).timestamp() * 1000),
        )
    except (ValueError, ZoneInfoNotFoundError):
        return None
    return datetime.fromtimestamp(next_ms / 1000, tz=UTC)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ScheduleKind",
    "ScheduleSpec",
    "next_run_at",
    "parse_schedule",
]
