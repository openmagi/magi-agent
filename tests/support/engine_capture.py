"""Capture MagiEngineDriver.run_turn_stream output as a JSON-safe snapshot.

``capture_engine_turn`` drives a single turn through ``MagiEngineDriver`` using
an injected MockRunner and returns a deterministic, JSON-serialisable snapshot::

    {
        "events":   [...],   # list of normalised RuntimeEvent dicts
        "terminal": {...},   # EngineResult fields, enum values as strings
    }

Volatile timing fields (``durationMs``, ``latency_ms``, ``ts``, …) are
replaced with the sentinel ``"<normalized>"`` so golden files are stable across
runs.

Shape notes (deviations from the brief template):
- ``RuntimeEvent`` is a **Pydantic BaseModel** (not a dataclass) — serialised
  via ``.model_dump()``, not ``dataclasses.asdict()``.
- ``EngineResult`` IS a dataclass, but its ``terminal`` field is a
  ``Terminal`` enum; ``_to_jsonable`` converts it to its ``.value`` string
  before recursing, which avoids a non-serialisable enum surviving into the
  snapshot.
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain

# Keys whose values are volatile (timestamps, wall-clock durations, …).
# Replace with a stable sentinel so snapshots are golden-file safe.
_VOLATILE_KEYS = frozenset(
    {
        "latency_ms",
        "durationMs",
        "duration_ms",
        "started_at",
        "ended_at",
        "ts",
        "observedAt",
        "observed_at",
        "emittedAt",
        "lastActivityAt",
    }
)


def _to_jsonable(obj: object) -> object:
    """Convert obj to a plain JSON-compatible structure.

    Handles Pydantic models (.model_dump), dataclasses (dataclasses.asdict
    with manual enum coercion), enums (.value), and falls back to vars() or
    the raw value for primitives.
    """
    if hasattr(obj, "model_dump"):
        # Pydantic model (e.g. RuntimeEvent) — mode="json" converts enums to
        # their .value strings and datetimes to ISO strings automatically.
        return obj.model_dump(mode="json")
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        # Manually convert to dict so we can handle nested enums correctly.
        result: dict[str, object] = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            result[f.name] = _to_jsonable(value)
        return result
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _normalize(value: object) -> object:
    """Replace volatile keys with ``"<normalized>"`` recursively."""
    if isinstance(value, dict):
        return {
            k: "<normalized>" if k in _VOLATILE_KEYS else _normalize(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


async def capture_engine_turn(
    turn_input: dict,
    runner: object,
    *,
    driver_kwargs: dict | None = None,
) -> dict:
    """Drive one engine turn and return a JSON-safe, normalised snapshot.

    Parameters
    ----------
    turn_input:
        Dict passed to ``MagiEngineDriver.run_turn_stream`` as the turn input
        (e.g. ``{"prompt": "…", "session_id": "s1", "turn_id": "t1"}``).
    runner:
        A ``MockRunner`` (or any object with a ``run_async`` async generator
        method) injected into the engine driver.
    driver_kwargs:
        Optional extra keyword arguments forwarded to ``MagiEngineDriver()``.

    Returns
    -------
    dict
        ``{"events": [...], "terminal": {...}}`` — fully JSON-serialisable and
        deterministic (volatile timing fields replaced with ``"<normalized>"``).
    """
    driver = MagiEngineDriver(runner=runner, **(driver_kwargs or {}))
    cancel = asyncio.Event()
    events, terminal = await drain(
        driver.run_turn_stream(None, turn_input, cancel=cancel)
    )
    return {
        "events": _normalize([_to_jsonable(e) for e in events]),
        "terminal": _normalize(_to_jsonable(terminal)),
    }
