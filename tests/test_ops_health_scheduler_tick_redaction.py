"""A-11 — ``scheduler_executor_health_projection`` lenient redaction.

The health surface used to fold caller-supplied ``tick_summary`` mappings
into the projection key-by-key with only a "do not overwrite a core key"
guard, so a noisy caller could leak credential-shaped values straight
into the publicly-projected health dict. A-11 routes ``tick_summary``
through a lenient health-surface scrub:

* core keys never change (regression net for legacy callers)
* finite numerics, booleans, and secret-clean strings pass through verbatim
* keys that violate ``require_safe_key`` are dropped (e.g. ``"token"``)
* values that the C-1 secret-marker denylist flags are dropped
* a noisy tick must never crash the health surface
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_scheduler_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear scheduler env vars so each test starts from a known baseline."""

    for var in (
        "MAGI_SCHEDULER_EXECUTOR_ENABLED",
        "MAGI_SCHEDULER_SHADOW",
        "MAGI_SCHEDULER_KILL_SWITCH_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Core projection unchanged for clean tick payloads.
# ---------------------------------------------------------------------------


def test_clean_tick_summary_survives_scrub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "1")
    from magi_agent.ops.health import scheduler_executor_health_projection

    tick = {
        "lastTickUtcIso": "2026-06-03T00:00:00+00:00",
        "fired": 3,
        "suppressed_silent": 1,
        "skipped": 2,
        "timed_out": 0,
        "lease_rejected": 0,
    }
    proj = scheduler_executor_health_projection(tick_summary=tick)
    assert proj["lastTickUtcIso"] == "2026-06-03T00:00:00+00:00"
    assert proj["fired"] == 3
    assert proj["suppressed_silent"] == 1
    assert proj["skipped"] == 2
    assert proj["timed_out"] == 0
    assert proj["lease_rejected"] == 0
    # Core keys still present and unchanged.
    assert proj["executorEnabled"] is True
    assert proj["status"] == "shadow"


def test_core_keys_unchanged_when_tick_collides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_SCHEDULER_SHADOW", raising=False)
    """A tick value that names a core projection key never overwrites it
    (back-compat guard preserved through the scrub)."""

    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"status": "live", "executorEnabled": True}
    )
    # Core derivation wins; tick override is silently ignored.
    assert proj["status"] == "disabled"
    assert proj["executorEnabled"] is False


# ---------------------------------------------------------------------------
# Secret-bearing values are dropped, not crashed.
# ---------------------------------------------------------------------------


def test_secret_value_dropped_not_surfaced() -> None:
    """A bearer-token value in a tick row is dropped from the surfaced
    projection; the health surface MUST NOT crash on a noisy tick."""

    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"leakyHeader": "Bearer eyJ.abcdefghij.xyz", "fired": 5}
    )
    assert "leakyHeader" not in proj
    # Sibling clean key still survives.
    assert proj["fired"] == 5


def test_provider_secret_value_dropped() -> None:
    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"providerNote": "sk-livekey0123456789abcdef", "fired": 2}
    )
    assert "providerNote" not in proj
    assert proj["fired"] == 2


def test_unsafe_key_dropped() -> None:
    """Even a clean value cannot rescue a key the safe-key vocabulary
    forbids (``token`` is on the unsafe-key denylist)."""

    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"token": "ok-looking-value", "fired": 1}
    )
    assert "token" not in proj
    assert proj["fired"] == 1


# ---------------------------------------------------------------------------
# Health surface MUST NOT crash on weird inputs.
# ---------------------------------------------------------------------------


def test_non_finite_float_dropped() -> None:
    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"jitterMs": float("inf"), "fired": 7}
    )
    assert "jitterMs" not in proj
    assert proj["fired"] == 7


def test_unknown_value_type_dropped() -> None:
    """A dict-valued tick row falls outside the lenient scrub's accepted
    primitives; the key is dropped silently rather than crashing."""

    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"nested": {"deep": "noise"}, "fired": 4}
    )
    assert "nested" not in proj
    assert proj["fired"] == 4


def test_none_value_dropped() -> None:
    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"maybeMissing": None, "fired": 9}
    )
    assert "maybeMissing" not in proj
    assert proj["fired"] == 9


# ---------------------------------------------------------------------------
# Containers: tuples/lists of primitives pass; mixed-secret tuples drop.
# ---------------------------------------------------------------------------


def test_clean_tuple_passes() -> None:
    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"batches": [1, 2, 3]}
    )
    assert proj["batches"] == (1, 2, 3)


def test_tuple_with_secret_dropped() -> None:
    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection(
        tick_summary={"recentTokens": ["sk-livekey0123456789abcdef", "ok"]}
    )
    assert "recentTokens" not in proj


# ---------------------------------------------------------------------------
# Absent tick_summary still omits the fields (existing contract).
# ---------------------------------------------------------------------------


def test_no_tick_summary_still_omits_fields() -> None:
    from magi_agent.ops.health import scheduler_executor_health_projection

    proj = scheduler_executor_health_projection()
    assert "lastTickUtcIso" not in proj
    assert "fired" not in proj
