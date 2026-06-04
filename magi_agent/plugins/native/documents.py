from __future__ import annotations

from magi_agent.plugins.native._common import blocked_result, digest, ok_result, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.tools.spreadsheet_tools import csv_write
from magi_agent.web_acquisition.policy import redact_public_text


def document_write(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    content = str(arguments.get("content") or arguments.get("text") or "")
    if not content.strip():
        return blocked_result("DocumentWrite", "content_required")
    path_value = arguments.get("path") or arguments.get("filename") or "magi-document.md"
    try:
        path = safe_child_path(context, path_value, default_name="magi-document.md")
    except ValueError as error:
        return blocked_result("DocumentWrite", str(error))
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_content = redact_public_text(content, max_chars=200_000)
    path.write_text(safe_content, encoding="utf-8")
    relative = path.relative_to(safe_child_path(context, ".", default_name=".")).as_posix()
    return ok_result(
        "DocumentWrite",
        {
            "path": relative,
            "pathRef": relative,
            "contentDigest": digest(safe_content),
            "byteCount": len(safe_content.encode("utf-8")),
            "localOnly": True,
        },
    )


def spreadsheet_write(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    args = dict(arguments)
    args.setdefault("path", "magi-spreadsheet.csv")
    if "rows" not in args:
        args["rows"] = [["value"], [str(args.get("content") or "")]]
    return csv_write(args, context)
