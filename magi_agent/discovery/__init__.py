"""Stateful iterative-discovery orchestrator + static template library.

This package absorbs the TIDE paper's mechanism (proactive multi-problem
discovery via cumulative-state conditioning over T rounds) as a purely additive
harness/orchestrator layer. It does NOT touch the core agent loop, turn control,
message building, or ``cli/real_runner.py`` — it only READS the runner seam used
by the GAIA harness.

Default-OFF: the orchestrator entry point is gated behind
``MAGI_DISCOVERY_ENABLED`` (see :mod:`magi_agent.discovery.gate`).
"""
from __future__ import annotations

__all__ = ()
