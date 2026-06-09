# magi_agent/egress_proxy/evidence.py
from __future__ import annotations

import logging
from collections.abc import Callable

from magi_agent.evidence.gate1a_egress_correlation import (
    GATE1A_EGRESS_TELEMETRY_SOURCE,
)

logger = logging.getLogger(__name__)


def egress_proxy_record(
    *,
    call_class: str,
    sink: Callable[[dict], None] | None = None,
) -> dict:
    """Build (and optionally emit) a digest-only egress-proxy decision record.

    Best-effort: emission failures are swallowed so a tool call is never broken.

    NOTE: sub-project A defines this seam but does not yet wire it into the two
    injection sites (gate5b Bash env, live_fetch httpx). Emission from a real
    runtime evidence sink lands in a follow-up; today no records are emitted.
    """
    record = {
        "evidence_source": GATE1A_EGRESS_TELEMETRY_SOURCE,
        "call_class": call_class,
        "decision": "routed_via_egress_proxy",
    }
    if sink is not None:
        try:
            sink(record)
        except Exception:  # noqa: BLE001 — best-effort telemetry
            logger.debug("egress proxy evidence sink failed", exc_info=True)
    return record
