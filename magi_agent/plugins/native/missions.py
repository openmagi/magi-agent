from __future__ import annotations

from magi_agent.plugins.native._common import digest, ok_result
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text


def mission_ledger(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    objective = redact_public_text(
        str(arguments.get("objective") or arguments.get("mission") or "local mission"),
        max_chars=500,
    )
    record = {
        "botId": context.bot_id,
        "sessionId": context.session_id,
        "objective": objective,
        "status": "local_recorded",
    }
    return ok_result("MissionLedger", {"record": record, "recordDigest": digest(record)})
