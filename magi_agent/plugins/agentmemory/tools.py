from __future__ import annotations

import json
from collections.abc import Mapping

from magi_agent.plugins.native._common import blocked_result, digest, ok_result, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text


def agentmemory_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    query = redact_public_text(str(arguments.get("query") or arguments.get("q") or ""), max_chars=256).strip()
    if not query:
        return blocked_result("AgentMemorySearch", "query_required")
    path = safe_child_path(
        context,
        ".magi/agentmemory.jsonl",
        default_name=".magi/agentmemory.jsonl",
        mutating=False,
        allow_internal=True,
    )
    if not path.exists():
        return ok_result("AgentMemorySearch", {"query": query, "matches": (), "memoryDigest": digest(())})
    matches: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if query.casefold() not in line.casefold():
            continue
        matches.append({"ref": f"memory:{len(matches) + 1}", "preview": redact_public_text(line, max_chars=300)})
        if len(matches) >= 10:
            break
    return ok_result("AgentMemorySearch", {"query": query, "matches": tuple(matches), "memoryDigest": digest(matches)})


def agentmemory_remember(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    content = redact_public_text(str(arguments.get("content") or arguments.get("text") or ""), max_chars=2000).strip()
    if not content:
        return blocked_result("AgentMemoryRemember", "content_required")
    path = safe_child_path(
        context,
        ".magi/agentmemory.jsonl",
        default_name=".magi/agentmemory.jsonl",
        allow_internal=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record: Mapping[str, object] = {
        "botId": context.bot_id,
        "sessionId": context.session_id,
        "contentDigest": digest(content),
        "content": content,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return ok_result("AgentMemoryRemember", {"recordDigest": digest(record), "pathRef": ".magi/agentmemory.jsonl"})
