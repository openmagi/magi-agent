# tests/benchmarks/taubench/test_config.py
from __future__ import annotations

from benchmarks.taubench.config import FULL_CAPABILITY_FLAGS, flags_for


def test_full_sets_the_six_control_plane_flags() -> None:
    flags = flags_for("full")
    assert flags == FULL_CAPABILITY_FLAGS
    assert flags["MAGI_SELF_REVIEW_ENABLED"] == "1"
    assert set(flags) == {
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
        "MAGI_SELF_REVIEW_ENABLED",
    }


def test_vanilla_sets_no_flags() -> None:
    assert flags_for("vanilla") == {}
