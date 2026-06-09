from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.memory.contracts import (
    MemoryProviderCapabilities,
    MemoryRecord,
    RecallRequest,
    RecallResult,
    UnsupportedMemoryOperationError,
)
from magi_agent.memory.policy import MemoryPolicy, evaluate_memory_policy

# NOTE: QmdClient is imported lazily inside the gated live-recall branch to keep
# the module import boundary free of network libraries (urllib is stdlib, but the
# adapter import-boundary test asserts no provider/network modules load at import
# time, so we resolve the symbol via this module attribute and import lazily).
from magi_agent.memory.qmd_client import QmdClient  # noqa: F401 (re-exported for monkeypatch seam)


PROVIDER_ID = "hipocampus-qmd-readonly"

#: Env gate for the OPTIONAL live qmd recall path (default OFF).  When unset/falsy,
#: ``_load_qmd_records`` behaves byte-identically to the pre-existing JSON-file path.
#: This is a SEPARATE surface from the shadow parity contracts that pin
#: ``hipocampus_qmd_live_called: Literal[False]`` — those guard fixture/projection
#: evidence, not this recall adapter.
MAGI_MEMORY_QMD_LIVE_ENABLED_ENV = "MAGI_MEMORY_QMD_LIVE_ENABLED"

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_DROP_SNIPPET_LINE_RE = re.compile(
    r"(?:raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"chain[ _-]?of[ _-]?thought|hidden[ _-]?reasoning|private[ _-]?reasoning|"
    r"raw provider path|private provider path|provider path|"
    r"qmd[ _-]?(?:index|cache)|\.qmd(?:/|\b)|/tmp/qmd(?:/|\b)|"
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|"
    r"-----BEGIN [A-Z0-9 ]*SECRET KEY-----|"
    r"-----BEGIN [A-Z0-9 ]*RSA PRIVATE KEY-----|"
    r"-----BEGIN [A-Z0-9 ]*DSA PRIVATE KEY-----|"
    r"-----BEGIN [A-Z0-9 ]*EC PRIVATE KEY-----|"
    r"-----BEGIN [A-Z0-9 ]*OPENSSH PRIVATE KEY-----|"
    r"-----END [A-Z0-9 ]*(?:PRIVATE|SECRET) KEY-----|"
    r"\b[A-Za-z0-9+/]{16,}={0,2}\b|"
    r"<(?:/?)(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)\b|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/private(?:/|\b)|/workspace(?:/|\b)|"
    r"[A-Za-z]:\\(?:Users|Documents and Settings|workspace)\\|"
    r"/data/bots(?:/|\b)|/var/lib/kubelet(?:/|\b)|"
    r"s3://|gs://|supabase://|postgres(?:ql)?://|api\.telegram\.org/bot|"
    r"X-Amz-Signature)",
    re.IGNORECASE,
)
_REDACT_SNIPPET_RE = re.compile(
    r"(?:authorization\s*:\s*[^\n\r]+|cookie\s*:\s*[^\n\r]+|"
    r"set-cookie\s*:\s*[^\n\r]+|bearer\s+[A-Za-z0-9._~+/=-]{4,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{4,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"glpat-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AKIA[A-Z0-9]{8,}|AIza[0-9A-Za-z_-]{20,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|ACCESS_KEY)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,}|session\s*=\s*[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")


class UnsafeMemoryPathError(ValueError):
    pass


class HipocampusReadOnlyConfig(BaseModel):
    model_config = _MODEL_CONFIG

    workspace_root: Path = Field(alias="workspaceRoot")
    enabled: bool = False
    qmd_results_path: str = Field(default="memory/qmd_results.json", alias="qmdResultsPath")
    daily_memory_glob: str = Field(default="memory/daily/*.md", alias="dailyMemoryGlob")
    max_records: int = Field(default=5, alias="maxRecords", ge=1, le=20)
    #: Collection scope used when the OPTIONAL live qmd recall gate is ON.
    #: Ignored entirely when the gate is OFF (the JSON-file path takes no collection).
    qmd_collection: str = Field(default="clawy-memory", alias="qmdCollection")
    #: Endpoint override for the live qmd client; falls back to MAGI_QMD_ENDPOINT.
    qmd_endpoint: str | None = Field(default=None, alias="qmdEndpoint")


class HipocampusReadOnlyAdapter:
    """Read-only local adapter for Hipocampus/qmd-shaped memory files.

    ADK owns live memory attachment through MemoryService. This adapter is only
    OpenMagi provider normalization and policy/audit plumbing; it never
    constructs ADK memory services, invokes qmd, writes memory, or projects
    memory into prompts.
    """

    prompt_projection_enabled: Literal[False] = False

    def __init__(self, config: HipocampusReadOnlyConfig) -> None:
        self.config = config
        self.workspace_root = _validate_workspace_root(config.workspace_root)

    def capabilities(self) -> MemoryProviderCapabilities:
        return MemoryProviderCapabilities(
            provider_id=PROVIDER_ID,
            storage_model="file",
            supports_search=True,
            supports_export=True,
            consistency="snapshot",
            max_result_bytes=32_768,
            policy_required=("memory_mode", "source_authority", "redaction"),
        )

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return self._query_memory(request, policy=policy, include_root=True)

    async def search(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return self._query_memory(request, policy=policy, include_root=False)

    async def remember(self, _payload: object) -> None:
        raise UnsupportedMemoryOperationError("remember", provider_id=PROVIDER_ID)

    async def delete(self, _record_id: str) -> None:
        raise UnsupportedMemoryOperationError("delete", provider_id=PROVIDER_ID)

    async def redact(self, _record_id: str) -> None:
        raise UnsupportedMemoryOperationError("redact", provider_id=PROVIDER_ID)

    async def compact(self, _record_ids: Sequence[str]) -> None:
        raise UnsupportedMemoryOperationError("compact", provider_id=PROVIDER_ID)

    async def erase(self, _record_id: str) -> None:
        raise UnsupportedMemoryOperationError("erase", provider_id=PROVIDER_ID)

    def _query_memory(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
        include_root: bool,
    ) -> RecallResult:
        decision = evaluate_memory_policy(request, policy)
        if not self.config.enabled:
            reason_codes = (*decision.reason_codes, "adapter_disabled")
            return _empty_result(decision, reason_codes=reason_codes)
        if not decision.recall_allowed:
            return _empty_result(decision)

        records: list[MemoryRecord] = []
        if include_root:
            root = self._load_root_record(request)
            if root is not None:
                records.append(root)
            records.extend(self._load_daily_records(request))

        records.extend(self._load_qmd_records(request))
        limited = _apply_output_budget(
            records[: min(request.limit, self.config.max_records)],
            max_bytes=request.max_bytes,
        )
        return RecallResult(
            provider_id=PROVIDER_ID,
            records=limited,
            recall_allowed=decision.recall_allowed,
            write_allowed=decision.write_allowed,
            prompt_projection_allowed=False,
            public_projection_allowed=decision.public_projection_allowed,
            reason_codes=decision.reason_codes,
        )

    def _load_root_record(self, request: RecallRequest) -> MemoryRecord | None:
        for rel_path in ("memory/ROOT.md", "MEMORY.md"):
            path = _resolve_workspace_path(self.workspace_root, rel_path)
            if path is None or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            if not _matches_query(content, request.query):
                continue
            return MemoryRecord(
                id=_digest_ref("hipocampus_root", rel_path),
                scope="bot",
                kind="note",
                body=_safe_snippet(content, request.max_bytes),
                source_ref=_digest_ref("hipocampus_root.source", rel_path),
                provider_id=PROVIDER_ID,
                confidence="observed",
                visibility="public-safe",
                score=1.0,
                custom_metadata={
                    "sourceKind": "hipocampus_root",
                    "sourceDigest": _digest_ref("hipocampus_root.source", rel_path),
                },
            )
        return None

    def _load_daily_records(self, request: RecallRequest) -> list[MemoryRecord]:
        if _unsafe_relative_pattern(self.config.daily_memory_glob):
            return []

        records: list[MemoryRecord] = []
        for path in sorted(self.workspace_root.glob(self.config.daily_memory_glob), reverse=True):
            if not path.is_file():
                continue
            try:
                rel_path = path.relative_to(self.workspace_root).as_posix()
            except ValueError:
                continue
            if not rel_path.startswith("memory/daily/") or not rel_path.endswith(".md"):
                continue
            if _resolve_workspace_path(self.workspace_root, rel_path) is None:
                continue
            content = path.read_text(encoding="utf-8")
            if not _matches_query(content, request.query):
                continue
            records.append(
                MemoryRecord(
                    id=_digest_ref("hipocampus_daily", rel_path),
                    scope="bot",
                    kind="note",
                    body=_safe_snippet(content, request.max_bytes),
                    source_ref=_digest_ref("hipocampus_daily.source", rel_path),
                    provider_id=PROVIDER_ID,
                    confidence="observed",
                    visibility="public-safe",
                    score=0.95,
                    custom_metadata={
                        "sourceKind": "hipocampus_daily",
                        "sourceDigest": _digest_ref("hipocampus_daily.source", rel_path),
                    },
                )
            )
        return records

    def _load_qmd_records(self, request: RecallRequest) -> list[MemoryRecord]:
        if _qmd_live_recall_enabled():
            results = self._live_qmd_results(request)
        else:
            results = self._json_qmd_results()
        return self._map_qmd_results(results, request)

    def _json_qmd_results(self) -> list[dict]:
        """Read the pre-computed qmd_results.json file (default, gate-OFF path)."""
        path = _resolve_workspace_path(self.workspace_root, self.config.qmd_results_path)
        if path is None or not path.is_file():
            return []
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        results = parsed.get("results") if isinstance(parsed, dict) else None
        if not isinstance(results, list):
            return []
        return [item for item in results if isinstance(item, dict)]

    def _live_qmd_results(self, request: RecallRequest) -> list[dict]:
        """Query qmd live (OPTIONAL, GATED path).

        Fail-open: ``QmdClient.query`` never raises into a turn; on any failure
        it returns ``[]`` and recall simply yields no qmd records.  Returns the
        same ``{"path","content","score","context"}`` shape as the JSON file so
        the shared mapper builds identical ``MemoryRecord`` objects.
        """
        client = QmdClient(endpoint=self.config.qmd_endpoint)
        return client.query(
            request.query,
            collection=self.config.qmd_collection,
            limit=min(request.limit, self.config.max_records),
            min_score=request.min_score,
        )

    def _map_qmd_results(
        self,
        results: list[dict],
        request: RecallRequest,
    ) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            rel_path = item.get("path")
            content = item.get("content")
            if not isinstance(rel_path, str) or not isinstance(content, str):
                continue
            if _resolve_workspace_path(self.workspace_root, rel_path) is None:
                continue
            score = item.get("score")
            if not isinstance(score, int | float) or score < request.min_score:
                continue
            if not _matches_query(content, request.query):
                continue
            source_digest = _digest_ref("qmd_search.source", rel_path)
            metadata: dict[str, object] = {
                "sourceKind": "qmd_search",
                "sourceDigest": source_digest,
            }
            context = item.get("context")
            if isinstance(context, str) and context.strip():
                metadata["contextDigest"] = _digest_ref("qmd_search.context", context)
            records.append(
                MemoryRecord(
                    id=_digest_ref("qmd_search", rel_path),
                    scope="bot",
                    kind="note",
                    body=_safe_snippet(content, request.max_bytes),
                    source_ref=source_digest,
                    provider_id=PROVIDER_ID,
                    confidence="observed",
                    visibility="public-safe",
                    score=float(score),
                    custom_metadata=metadata,
                )
            )
        records.sort(key=lambda record: record.score or 0, reverse=True)
        return records


def _qmd_live_recall_enabled() -> bool:
    """Return True when the OPTIONAL live qmd recall gate is set to a truthy value.

    Default (unset/falsy): False — the JSON-file path is used byte-identically.
    """
    return os.environ.get(MAGI_MEMORY_QMD_LIVE_ENABLED_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _empty_result(
    decision: object,
    *,
    reason_codes: tuple[str, ...] | None = None,
) -> RecallResult:
    return RecallResult(
        provider_id=PROVIDER_ID,
        records=(),
        recall_allowed=getattr(decision, "recall_allowed"),
        write_allowed=getattr(decision, "write_allowed"),
        prompt_projection_allowed=False,
        public_projection_allowed=getattr(decision, "public_projection_allowed"),
        reason_codes=reason_codes or getattr(decision, "reason_codes"),
    )


def _validate_workspace_root(path: Path) -> Path:
    raw = str(path)
    if _PRODUCTION_PATH_RE.search(raw):
        raise UnsafeMemoryPathError("read-only memory adapter cannot point at production paths")
    resolved = path.expanduser().resolve(strict=False)
    if _PRODUCTION_PATH_RE.search(str(resolved)):
        raise UnsafeMemoryPathError("read-only memory adapter cannot resolve to production paths")
    return resolved


def _resolve_workspace_path(workspace_root: Path, rel_path: str) -> Path | None:
    if _PRODUCTION_PATH_RE.search(rel_path) or Path(rel_path).is_absolute():
        return None
    try:
        full = (workspace_root / rel_path).resolve(strict=False)
        full.relative_to(workspace_root)
    except ValueError:
        return None
    return full


def _unsafe_relative_pattern(pattern: str) -> bool:
    return (
        bool(_PRODUCTION_PATH_RE.search(pattern))
        or Path(pattern).is_absolute()
        or any(part == ".." for part in Path(pattern).parts)
        or not pattern.startswith("memory/daily/")
        or not pattern.endswith(".md")
    )


def _matches_query(content: str, query: str) -> bool:
    terms = [term for term in re.findall(r"[\w가-힣]+", query.lower()) if len(term) >= 2]
    if not terms:
        return True
    lowered = content.lower()
    return any(term in lowered for term in terms)


def _safe_snippet(content: str, max_bytes: int) -> str:
    lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or _MARKDOWN_HEADING_RE.match(line):
            continue
        if _DROP_SNIPPET_LINE_RE.search(line):
            break
        redacted = _REDACT_SNIPPET_RE.sub("[redacted]", line)
        if redacted:
            lines.append(redacted)
    return _cap("\n".join(lines), max_bytes)


def _apply_output_budget(
    records: Sequence[MemoryRecord],
    *,
    max_bytes: int,
) -> tuple[MemoryRecord, ...]:
    if max_bytes < 1:
        return ()

    output: list[MemoryRecord] = []
    remaining = max_bytes
    for record in records:
        body = _cap(record.body, remaining)
        if not body:
            continue
        output.append(record.model_copy(update={"body": body}))
        remaining -= len(body.encode("utf-8"))
        if remaining <= 0:
            break
    return tuple(output)


def _cap(content: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content
    if max_bytes <= 3:
        return encoded[:max_bytes].decode("utf-8", errors="ignore")
    return encoded[: max(0, max_bytes - 3)].decode("utf-8", errors="ignore") + "..."


def _digest_ref(kind: str, source_ref: str) -> str:
    digest = hashlib.sha256(f"{kind}:{source_ref}".encode("utf-8")).hexdigest()
    return f"memory:sha256:{digest}"
