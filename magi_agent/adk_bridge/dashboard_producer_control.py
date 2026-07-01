"""Deny-on-present after-tool producer for dashboard-authored custom checks.

Mirrors the SHACL constraint verifier's producer/consumer split. On every
after-tool dispatch this control reads the on-disk ``dashboard-checks.json``
sidecar (the producer side of a dashboard pack) and, when an enabled check's
trigger matches the tool + result, emits a ``custom:DashboardCheck``
:class:`~magi_agent.evidence.types.EvidenceRecord`:

- ``action == "block"`` → top-level ``status="failed"`` (a violation; the
  pre-final verifier-bus dashboard gate blocks the final answer).
- ``action == "audit"`` → top-level ``status="ok"`` (observability; never
  blocks).

No match / tool-not-run → no record → no block. The record is appended to the
SAME ``LocalToolEvidenceCollector`` corpus that ``collect_for_turn`` (and thus
the gate) reads, keyed by the same ``(session_id, turn_id)`` normal tool
evidence uses.

This control is emit-only: ``on_after_tool`` ALWAYS returns ``None`` (no result
override) and its ENTIRE body is wrapped so a malformed sidecar or any other
fault can never break a turn.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from magi_agent.adk_bridge.control_plane import BaseLoopControl

DASHBOARD_PRODUCER_CONTROL_NAME = "magi_dashboard_producer"


def _default_search_bases() -> Sequence[Path]:
    from magi_agent.packs.discovery import default_search_bases  # noqa: PLC0415

    return default_search_bases()


class DashboardProducerControl(BaseLoopControl):
    """After-tool deny-on-present producer for dashboard custom checks."""

    name = DASHBOARD_PRODUCER_CONTROL_NAME

    def __init__(
        self,
        *,
        collector: Any,
        search_bases: Callable[[], Sequence[Path]] | None = None,
    ) -> None:
        self._collector = collector
        self._search_bases = search_bases or _default_search_bases
        # mtime-keyed sidecar cache: sidecar path -> (mtime_ns, [checks]).
        self._cache: dict[str, tuple[int, list[Any]]] = {}

    async def on_after_tool(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        try:
            import time  # noqa: PLC0415

            from magi_agent.config.env import (  # noqa: PLC0415
                is_dashboard_pack_authoring_enabled,
            )

            if not is_dashboard_pack_authoring_enabled():
                return None

            from magi_agent.evidence.types import EvidenceRecord  # noqa: PLC0415
            from magi_agent.packs.dashboard_authored import (  # noqa: PLC0415
                DASHBOARD_PACK_DIR_NAME,
                read_sidecar,
            )

            tool_name = getattr(tool, "name", "") or ""
            result_text = (
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False, default=str)
            )
            turn_id = getattr(tool_context, "invocation_id", None) or "local-turn"
            session_id = (
                getattr(getattr(tool_context, "session", None), "id", None)
                or "cli-session"
            )

            # PR-D3: an active mode may force-activate a dashboard check for this
            # turn via its scoped_policy_ids (even if the check is globally
            # disabled). Resolved once; validated per-base against that base's
            # real checks so an unknown scoped id simply never matches. Gated by
            # is_dashboard_pack_authoring_enabled() above, so force-include never
            # bypasses the operator flag. Empty when no mode is active.
            from magi_agent.customize.scoped_policy import (  # noqa: PLC0415
                active_scoped_policy_ids,
                resolve_scoped_policy_overlay,
            )

            _scoped_policy_ids = active_scoped_policy_ids()

            for base in self._search_bases():
                pack_root = Path(base) / DASHBOARD_PACK_DIR_NAME
                sidecar = pack_root / "dashboard-checks.json"
                if not sidecar.exists():
                    continue
                checks = self._load_checks(sidecar, pack_root, read_sidecar)
                forced_check_ids: frozenset[str] = frozenset()
                if _scoped_policy_ids:
                    forced_check_ids = frozenset(
                        resolve_scoped_policy_overlay(
                            _scoped_policy_ids,
                            custom_rules=(),
                            dashboard_check_ids={c.id for c in checks},
                        ).dashboard_check_ids
                    )
                for check in checks:
                    if not (check.enabled or check.id in forced_check_ids):
                        continue
                    if check.trigger.tool != tool_name:
                        continue
                    if not _matches(check.trigger.match, result_text):
                        continue
                    record = EvidenceRecord.model_validate(
                        {
                            "type": "custom:DashboardCheck",
                            "status": (
                                "failed" if check.action == "block" else "ok"
                            ),
                            "observedAt": int(time.time() * 1000),
                            "source": {
                                "kind": "tool_trace",
                                "toolName": tool_name,
                            },
                            "fields": {
                                "evidenceRef": f"evidence:dashboard:{check.id}",
                                "ruleId": check.id,
                                "action": check.action,
                            },
                        }
                    )
                    self._collector.append_evidence_record_for_turn(
                        session_id=session_id,
                        turn_id=turn_id,
                        record=record,
                    )
            return None
        except Exception:
            return None

    def _load_checks(
        self, sidecar: Path, pack_root: Path, read_sidecar: Callable[[Path], list[Any]]
    ) -> list[Any]:
        try:
            mtime = sidecar.stat().st_mtime_ns
        except OSError:
            return read_sidecar(pack_root)
        key = str(sidecar)
        cached = self._cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        checks = read_sidecar(pack_root)
        self._cache[key] = (mtime, checks)
        return checks


def _matches(match: Any, text: str) -> bool:
    pattern = match.pattern
    if match.is_regex:
        try:
            return re.search(pattern, text) is not None
        except re.error:
            return False
    return pattern in text


__all__ = ["DASHBOARD_PRODUCER_CONTROL_NAME", "DashboardProducerControl"]
