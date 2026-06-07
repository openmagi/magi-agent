from __future__ import annotations

import os

__all__ = ["executor_enabled", "channel_workflows_enabled"]

_TRUTHY = {"1", "true", "yes", "on"}


def executor_enabled() -> bool:
    """True iff MAGI_WORKFLOW_EXECUTOR_ENABLED is set to a truthy value. Default OFF."""
    return os.environ.get("MAGI_WORKFLOW_EXECUTOR_ENABLED", "").strip().lower() in _TRUTHY


def channel_workflows_enabled() -> bool:
    """True iff MAGI_CHANNEL_WORKFLOWS_ENABLED is set to a truthy value. Default OFF.
    Gates whether the channel surface engages workflows at all (separate from
    whether the executor can run)."""
    return os.environ.get("MAGI_CHANNEL_WORKFLOWS_ENABLED", "").strip().lower() in _TRUTHY
