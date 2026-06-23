from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# I-2 PR A: per-module ``_truthy`` removed in favour of the canonical leaf
# so the truthy set lives in exactly one place.
from magi_agent.config._truthy import is_true as _truthy


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    db_path: Path
    retention_days: int = 7
    max_events: int = 200_000
    health_interval_s: int = 5
    mission_interval_s: int = 30
    channel_interval_s: int = 10
    replay_buffer: int = 200

    @classmethod
    def from_env(cls, *, home: Path) -> "ObservabilityConfig":
        # I-4: routed through the typed flag registry.
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        return cls(
            enabled=flag_bool("MAGI_OBSERVABILITY_ENABLED"),
            db_path=home / "observability.db",
            retention_days=_int_env("MAGI_OBS_RETENTION_DAYS", 7),
            max_events=_int_env("MAGI_OBS_MAX_EVENTS", 200_000),
            health_interval_s=_int_env("MAGI_OBS_HEALTH_INTERVAL_S", 5),
            mission_interval_s=_int_env("MAGI_OBS_MISSION_INTERVAL_S", 30),
            channel_interval_s=_int_env("MAGI_OBS_CHANNEL_INTERVAL_S", 10),
            replay_buffer=_int_env("MAGI_OBS_REPLAY_BUFFER", 200),
        )
