from __future__ import annotations

import json

from magi_agent.plugins.native._common import digest, ok_result, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult


def artifact_update(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    artifact_id = str(arguments.get("artifactId") or arguments.get("id") or "local-artifact")
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {}
    path = safe_child_path(
        context,
        ".magi/artifacts.jsonl",
        default_name=".magi/artifacts.jsonl",
        allow_internal=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "operation": "update",
        "artifactId": artifact_id,
        "metadataDigest": digest(metadata),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return ok_result("ArtifactUpdate", {"artifactId": artifact_id, "recordDigest": digest(record)})


def artifact_delete(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    artifact_id = str(arguments.get("artifactId") or arguments.get("id") or "local-artifact")
    path = safe_child_path(
        context,
        ".magi/artifacts.jsonl",
        default_name=".magi/artifacts.jsonl",
        allow_internal=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"operation": "delete", "artifactId": artifact_id}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return ok_result("ArtifactDelete", {"artifactId": artifact_id, "recordDigest": digest(record)})
