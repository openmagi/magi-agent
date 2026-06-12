"""Multi-problem discovery benchmark harness (TIDE multi-bug, driver-only).

This package MEASURES the discovery feature (``magi_agent/discovery/``) on
multi-bug repository instances. It is a *driver* over the existing discovery
orchestrator — it adds no runtime behaviour and edits nothing in the core agent
loop or the ``discovery`` package. The split mirrors the GAIA harness
(``benchmarks/gaia``): dataset / harness / pure-scorer / resumable
run, plus a default-OFF env gate.
"""
from __future__ import annotations
