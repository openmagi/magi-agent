# benchmarks/taubench/config.py
from __future__ import annotations

from typing import Literal

Config = Literal["full", "vanilla"]

# The six default-OFF control-plane flags that build_default_plane reads.
FULL_CAPABILITY_FLAGS: dict[str, str] = {
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
    "MAGI_LOOP_GUARD_ENABLED": "1",
    "MAGI_ERROR_RECOVERY_ENABLED": "1",
    "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
    "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
    "MAGI_SELF_REVIEW_ENABLED": "1",
}


def flags_for(config: Config) -> dict[str, str]:
    return dict(FULL_CAPABILITY_FLAGS) if config == "full" else {}
