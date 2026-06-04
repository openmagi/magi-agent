from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from magi_agent.plugins.native._common import blocked_result, digest, ok_result, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def batch_read(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    paths = arguments.get("paths")
    if not isinstance(paths, list | tuple):
        return blocked_result("BatchRead", "paths_required")
    records: list[dict[str, object]] = []
    for item in paths[:20]:
        try:
            path = safe_child_path(
                context,
                item,
                default_name="README.md",
                mutating=False,
            )
            text = path.read_text(encoding="utf-8", errors="replace")[:4096]
        except (OSError, ValueError) as error:
            records.append({"path": str(item), "status": "blocked", "reason": str(error)})
            continue
        records.append({"path": path.name, "status": "ok", "contentDigest": digest(text)})
    return ok_result("BatchRead", {"records": records, "recordCount": len(records)})


def date_range(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    days = _int_value(arguments.get("days"), default=7, minimum=1, maximum=366)
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(days=days)
    return ok_result(
        "DateRange",
        {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "days": days,
        },
    )


def external_source_cache(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    uri = str(arguments.get("uri") or arguments.get("url") or "source:local")
    content = str(arguments.get("content") or "")
    path = safe_child_path(
        context,
        ".magi/external-sources.jsonl",
        default_name=".magi/external-sources.jsonl",
        allow_internal=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"uriDigest": digest(uri), "contentDigest": digest(content)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return ok_result("ExternalSourceCache", {"recordDigest": digest(record), "pathRef": ".magi/external-sources.jsonl"})


def external_source_read(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    uri = str(arguments.get("uri") or arguments.get("url") or "source:local")
    return ok_result(
        "ExternalSourceRead",
        {
            "uriDigest": digest(uri),
            "status": "available",
            "content": "Local source ledger placeholder. Configure a source provider for live retrieval.",
        },
    )


def _int_value(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
