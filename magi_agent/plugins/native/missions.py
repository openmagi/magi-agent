from __future__ import annotations

import os
from collections.abc import Mapping

from magi_agent.config.env import _is_true, native_receipts_honest
from magi_agent.plugins.native._common import blocked_result, digest, ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text

# Mission-backing attachment flag. Owned by the mission/always-on cluster
# (03/01); until that cluster wires a real mission ledger backing it stays
# unset, so the honest branch fires. When set, the handler routes past the
# honest block to the (cluster-owned) live delegation seam.
MISSION_LEDGER_ATTACHED_ENV = "MAGI_MISSION_LEDGER_ATTACHED"


def _env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _mission_ledger_attached(env: Mapping[str, str] | None = None) -> bool:
    return _is_true(_env(env).get(MISSION_LEDGER_ATTACHED_ENV))


def _local_mission_ledger_dir() -> "object | None":
    """Resolve the durable local mission backing (same dir as the evidence ledger).

    Returns a Path when the default-ON durable evidence directory is active
    (``MAGI_EVIDENCE_LEDGER_DIR`` semantics: path override, ``off`` disables,
    default ``<cwd>/.magi/evidence``), else None.
    """
    from pathlib import Path  # noqa: PLC0415

    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    # I-4: routed through the typed flag registry.
    raw_dir = (flag_str("MAGI_EVIDENCE_LEDGER_DIR") or "").strip()
    if raw_dir.lower() in ("off", "0", "false", "none", "disable", "disabled"):
        return None
    return Path(raw_dir) if raw_dir else Path.cwd() / ".magi" / "evidence"


def mission_ledger(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    objective = redact_public_text(
        str(arguments.get("objective") or arguments.get("mission") or "local mission"),
        max_chars=500,
    )
    # B5: the durable local evidence dir is a first-class mission backing —
    # records persist to missions.jsonl, so a fresh install (ledger default-ON)
    # has working missions instead of a contract-only block.
    ledger_dir = _local_mission_ledger_dir()
    if (
        native_receipts_honest()
        and not _mission_ledger_attached()
        and ledger_dir is None
    ):
        return blocked_result("MissionLedger", "mission_ledger_not_configured")
    record = {
        "botId": context.bot_id,
        "sessionId": context.session_id,
        "objective": objective,
        "status": "local_recorded",
    }
    persisted = False
    if ledger_dir is not None:
        import json as _json  # noqa: PLC0415

        try:
            ledger_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[attr-defined]
            with (ledger_dir / "missions.jsonl").open("a", encoding="utf-8") as handle:  # type: ignore[operator]
                handle.write(
                    _json.dumps({"record": record}, sort_keys=True, default=str) + "\n"
                )
            persisted = True
        except OSError:
            persisted = False
    return ok_result(
        "MissionLedger",
        {"record": record, "recordDigest": digest(record), "persisted": persisted},
    )
