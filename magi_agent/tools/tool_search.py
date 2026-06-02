"""ToolSearchTool — search the tool registry by keyword or exact name.

Implements Claude Code's ToolSearch pattern: when the tool pool is large,
deferred tools are sent to the LLM with only their name. The model calls
ToolSearchTool to load full schemas on demand.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .manifest import ToolManifest
from .registry import ToolRegistry
from .schema_projection import (
    contains_private_schema_text,
    project_public_tool_schema,
    redact_public_schema_text,
)


_SELECT_PREFIX = "select:"


class ToolSearchTool:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[dict[str, object]]:
        if max_results <= 0:
            return []

        if query.startswith(_SELECT_PREFIX):
            return self._select(query[len(_SELECT_PREFIX) :])

        return self._keyword_search(query, max_results=max_results)

    def _select(self, names_csv: str) -> list[dict[str, object]]:
        names = [n.strip() for n in names_csv.split(",") if n.strip()]
        results: list[dict[str, object]] = []
        for name in names:
            manifest = self._registry.resolve(name)
            if manifest is not None:
                results.append(_manifest_to_schema(manifest))
        return results

    def _keyword_search(
        self,
        query: str,
        *,
        max_results: int,
    ) -> list[dict[str, object]]:
        all_tools = self._registry.list_all()
        scored: list[tuple[float, ToolManifest]] = []

        query_lower = query.lower()
        query_words = re.findall(r"\w+", query_lower)

        for manifest in all_tools:
            score = _score_manifest(manifest, query_lower, query_words)
            if score > 0:
                scored.append((score, manifest))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            _manifest_to_schema(manifest)
            for _, manifest in scored[:max_results]
        ]


class ToolSearchConfig(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    enabled: bool = False
    max_results: int = Field(default=5, alias="maxResults", ge=1, le=50)


class ToolSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    query: str = ""
    selected_tool_names: tuple[str, ...] = Field(default=(), alias="selectedToolNames")


class ToolSearchDecision(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    status: str
    results: tuple[dict[str, object], ...] = ()
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "tools": [_public_tool_projection(result) for result in self.results],
            "reasonCodes": list(self.reason_codes),
        }


class ToolSearchBoundary:
    """Public-safe projection boundary for searched tool manifests."""

    def __init__(self, config: ToolSearchConfig) -> None:
        self.config = config

    def search(
        self,
        manifests: Sequence[ToolManifest],
        request: ToolSearchRequest,
    ) -> ToolSearchDecision:
        if not self.config.enabled:
            return ToolSearchDecision(status="disabled", reasonCodes=("tool_search_disabled",))

        selected = set(request.selected_tool_names)
        candidates = [
            manifest
            for manifest in manifests
            if not selected or manifest.name in selected
        ]
        if request.query.strip():
            query_lower = request.query.casefold()
            query_words = re.findall(r"\w+", query_lower)
            scored = [
                (_score_manifest(manifest, query_lower, query_words), manifest)
                for manifest in candidates
            ]
            candidates = [
                manifest
                for score, manifest in sorted(scored, key=lambda pair: pair[0], reverse=True)
                if score > 0
            ]

        results = tuple(
            _manifest_to_schema(manifest)
            for manifest in candidates[: self.config.max_results]
        )
        return ToolSearchDecision(status="ok", results=results)


def _score_manifest(
    manifest: ToolManifest,
    query_lower: str,
    query_words: Sequence[str],
) -> float:
    score = 0.0
    name_lower = manifest.name.lower()

    if name_lower == query_lower:
        score += 10.0
    elif query_lower in name_lower:
        score += 5.0

    desc_lower = manifest.description.lower()
    for word in query_words:
        if word in desc_lower:
            score += 2.0

    for tag in manifest.tags:
        if query_lower in tag.lower():
            score += 1.0

    return score


def _manifest_to_schema(manifest: ToolManifest) -> dict[str, object]:
    return {
        "name": manifest.name,
        "description": manifest.description,
        "input_schema": manifest.input_schema,
        "permission": manifest.permission,
        "kind": manifest.kind,
        "tags": list(manifest.tags),
        "dangerous": manifest.dangerous,
        "parallel_safety": manifest.parallel_safety,
    }


def _public_tool_projection(result: dict[str, object]) -> dict[str, object]:
    name = str(result.get("name", "tool"))
    tool_ref = f"tool:{_safe_digest(name)[:16]}"
    description = _redact_tool_name(str(result.get("description", "")), tool_name=name)
    input_schema = _redact_tool_name_from_value(result.get("input_schema"), tool_name=name)
    public: dict[str, object] = {
        "toolRef": tool_ref,
        "description": redact_public_schema_text(description),
        "inputSchema": project_public_tool_schema(input_schema),
    }
    for key in ("permission", "kind", "parallel_safety"):
        value = result.get(key)
        if isinstance(value, str):
            public[key] = value
    tags = result.get("tags")
    if isinstance(tags, list):
        safe_tags = [
            redact_public_schema_text(_redact_tool_name(tag, tool_name=name))
            for tag in tags
            if isinstance(tag, str) and not contains_private_schema_text(tag)
        ]
        if safe_tags:
            public["tags"] = safe_tags[:16]
    dangerous = result.get("dangerous")
    if isinstance(dangerous, bool):
        public["dangerous"] = dangerous
    return public


def _redact_tool_name_from_value(value: object, *, tool_name: str) -> object:
    if isinstance(value, str):
        return _redact_tool_name(value, tool_name=tool_name)
    if isinstance(value, dict):
        return {
            _redact_tool_name(str(key), tool_name=tool_name): _redact_tool_name_from_value(
                item,
                tool_name=tool_name,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_tool_name_from_value(item, tool_name=tool_name) for item in value]
    return value


def _redact_tool_name(value: str, *, tool_name: str) -> str:
    if not tool_name:
        return value
    return value.replace(tool_name, "[redacted-tool-name]")


def _safe_digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ToolSearchBoundary",
    "ToolSearchConfig",
    "ToolSearchDecision",
    "ToolSearchRequest",
    "ToolSearchTool",
]
