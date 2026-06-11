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


def mission_ledger(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    objective = redact_public_text(
        str(arguments.get("objective") or arguments.get("mission") or "local mission"),
        max_chars=500,
    )
    if native_receipts_honest() and not _mission_ledger_attached():
        return blocked_result("MissionLedger", "mission_ledger_not_configured")
    record = {
        "botId": context.bot_id,
        "sessionId": context.session_id,
        "objective": objective,
        "status": "local_recorded",
    }
    return ok_result("MissionLedger", {"record": record, "recordDigest": digest(record)})
