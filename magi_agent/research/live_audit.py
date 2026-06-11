"""Live, observe-only research-governance audit (audit-first).

The research governance machinery (claim graph / citation audit / final
projection gate) historically ran only inside the fixture-sealed harness — the
live CLI loop imported nothing from ``magi_agent.research``. This module is the
first live consumer: a deterministic citation audit over a finished turn.

Design constraints (from the GAIA measurement learnings):

* **Audit-first** — observe and report, never mutate or block. Blind ``enforce``
  measurably over-corrected (e.g. rewrote correct answers); enforcement is a
  future step gated on measured evidence, so the mode parser does not even
  accept ``enforce`` yet.
* **Deterministic, model-free** — no extra provider calls, no latency on the
  answer path. The audit compares URLs cited in the final answer against URLs
  actually observed in the turn's web-tool results.
* **Default OFF** — ``MAGI_RESEARCH_GOVERNANCE_MODE=audit`` opts in; anything
  else (including unknown values) is ``off`` and the turn is byte-identical.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping

RESEARCH_GOVERNANCE_MODE_ENV = "MAGI_RESEARCH_GOVERNANCE_MODE"

#: Tools whose results count as web evidence sources.
_WEB_SOURCE_TOOL_NAMES = frozenset(
    {"web_search", "web_fetch", "research_fact", "WebSearch", "WebFetch", "BrowserTask"}
)

_URL_RE = re.compile(r"https?://[^\s\"'<>\\)\]]+")


def research_governance_mode(env: Mapping[str, str] | None = None) -> str:
    """Return ``"audit"`` or ``"off"`` (the only modes that exist today).

    ``enforce`` deliberately resolves to ``off``: enforcement must be a
    measured, separate step — silently treating it as audit would misrepresent
    the operator's intent, and blocking without evidence is a known regression.
    """
    source = os.environ if env is None else env
    raw = (source.get(RESEARCH_GOVERNANCE_MODE_ENV) or "").strip().lower()
    return "audit" if raw == "audit" else "off"


def _extract_urls(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _URL_RE.findall(text or ""):
        seen.setdefault(match.rstrip(".,;:!?"), None)
    return list(seen)


class ResearchLiveAudit:
    """Collects web-source URLs from a turn's tool events, then audits the answer."""

    def __init__(self) -> None:
        self._tool_names: dict[str, str] = {}
        self._source_urls: dict[str, None] = {}

    def observe_event(self, event_type: str, payload: Mapping[str, object]) -> None:
        """Feed a runtime event; only web-tool starts/ends are recorded."""
        if event_type != "tool" or not isinstance(payload, Mapping):
            return
        inner = str(payload.get("type") or "")
        tool_id = str(payload.get("id") or "")
        if inner == "tool_start":
            name = str(
                payload.get("name") or payload.get("toolName") or payload.get("tool") or ""
            )
            if name:
                self._tool_names[tool_id] = name
            return
        if inner != "tool_end":
            return
        name = self._tool_names.get(tool_id, "")
        if name not in _WEB_SOURCE_TOOL_NAMES:
            return
        try:
            rendered = json.dumps(payload, default=str)
        except (TypeError, ValueError):
            rendered = str(payload)
        for url in _extract_urls(rendered):
            self._source_urls.setdefault(url, None)

    def report(self, final_text: str) -> dict[str, object]:
        """Deterministic audit report; never raises, never blocks."""
        cited = _extract_urls(final_text)
        sources = list(self._source_urls)
        cited_without_source = [url for url in cited if url not in self._source_urls]
        cited_set = set(cited)
        sources_uncited = [url for url in sources if url not in cited_set]
        return {
            "type": "research_governance_audit",
            "mode": "audit",
            "sourceUrlCount": len(sources),
            "citedUrlCount": len(cited),
            "citedWithoutSource": cited_without_source,
            "sourcesUncited": sources_uncited,
            "verdict": "attention" if cited_without_source else "pass",
        }


__all__ = [
    "RESEARCH_GOVERNANCE_MODE_ENV",
    "ResearchLiveAudit",
    "research_governance_mode",
]
