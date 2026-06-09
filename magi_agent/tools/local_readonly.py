from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from magi_agent.evidence.runtime_receipts import (
    ReceiptAuthorityFlags,
    SourceEvidenceReceipt,
    ToolExecutionReceipt,
)
from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    public_source_ledger_report,
)

from .context import ToolContext
from .memory_mode_guard import (
    is_incognito_memory_mode,
    is_long_term_memory_read_disabled,
    is_protected_memory_path,
    protected_memory_error,
)
from .result import ToolResult


LOCAL_READONLY_TOOL_NAMES = ("FileRead", "Glob", "Grep", "GitDiff")
_SOURCE_INSPECTION_TOOL_NAMES = frozenset(LOCAL_READONLY_TOOL_NAMES)
_DEFAULT_MAX_READ_BYTES = 8192
_DEFAULT_MAX_MATCHES = 64
_DEFAULT_MAX_FILES = 128
_DEFAULT_MAX_SNIPPET_CHARS = 160
_MAX_READ_BYTES_LIMIT = 64 * 1024
_MAX_MATCH_LIMIT = 512
_MAX_FILE_LIMIT = 1024
_DIGEST_PREFIX_LENGTH = 24
_SENSITIVE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*[^\n\r]+|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bcookie\s*:\s*[^\n\r]+|"
    r"\bset-cookie\s*:\s*[^\n\r]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"(?:(?:api[_-]?key|password|secret|token|session(?:[_-]?(?:key|id)|key|id))"
    r"\s*[:=]\s*|session\s*=\s*)[^\s,;}\"']+|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/private/var(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args|text)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_PATH_PART_RE = re.compile(
    r"(?:"
    r"^\.|"
    r"(?:^|[._/-])(?:auth|config|cookie|credential|credentials|env|keys?|kube|"
    r"kubeconfig|password|private(?:key)?|secrets?|sessions?|tokens?|api[_-]?keys?)"
    r"(?:[._/-]|s?(?:\\.[A-Za-z0-9]+)?$)|"
    r"^(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|\.netrc|\.npmrc|\.pypirc)$"
    r")",
    re.IGNORECASE,
)
_PROTECTED_RELATIVE_PATHS = frozenset(
    {
        "agents.md",
        "claude.md",
        "heartbeat.md",
        "implement.md",
        "goal.md",
        "progress.md",
        "scratchpad.md",
        "soul.md",
        "standards.md",
        "task-queue.md",
        "tools.md",
        "working.md",
    }
)
_PROTECTED_RELATIVE_PREFIXES = (
    "memory/",
    "docs/superpowers/plans/",
)
_DIFF_PATH_PREFIX_RE = re.compile(r"^(?:a|b)/")
_AGENT_ROLES = frozenset({"coding", "general", "research"})


@dataclass(frozen=True)
class _ResolvedPath:
    path: Path
    relative: str
    path_ref: str


@dataclass(frozen=True)
class _SourceBundle:
    source_refs: tuple[str, ...]
    projection: dict[str, object]
    receipts: tuple[dict[str, object], ...]


class LocalReadOnlyToolHost:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        agent_role: str = "general",
        diff_fixtures: Mapping[str, str] | None = None,
        read_quality_enabled: bool | None = None,
    ) -> None:
        self._call_log: list[str] = []
        self._ledgers: dict[tuple[str, str], LocalResearchSourceLedger] = {}
        # PR14 — readonly handlers (FileRead/Glob/Grep/GitDiff) are offloaded to
        # threadpool threads via asyncio.to_thread when
        # MAGI_TOOL_CONCURRENCY_ENABLED=1, so a single host instance can run
        # concurrent reads. Guard the shared mutable state (the call log and the
        # per-(session,turn) ledger map) so concurrent reads cannot lose a call
        # record or double-create a ledger (get-then-set race). The lock is held
        # only around the in-memory mutation, never across file I/O.
        self._state_lock = threading.Lock()
        self._agent_role = _coerce_agent_role(agent_role)
        self._diff_fixtures = dict(diff_fixtures or {})
        if read_quality_enabled is None:
            from magi_agent.config.env import is_read_quality_enabled

            read_quality_enabled = is_read_quality_enabled()
        self._read_quality_enabled = bool(read_quality_enabled)

    @property
    def call_log(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(self._call_log)

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        with self._state_lock:
            self._call_log.append(tool_name)
        if tool_name not in _SOURCE_INSPECTION_TOOL_NAMES:
            return _blocked_result(tool_name, "local_readonly_tool_not_supported")

        try:
            if tool_name == "FileRead":
                return self._file_read(arguments, context)
            if tool_name == "Glob":
                return self._glob(arguments, context)
            if tool_name == "Grep":
                return self._grep(arguments, context)
            if tool_name == "GitDiff":
                return self._git_diff(arguments, context)
        except _PathPolicyError as error:
            return ToolResult(
                status="blocked",
                errorCode=error.reason_code,
                errorMessage="path policy denied",
                metadata=_base_metadata(tool_name, reason=error.reason_code),
            )
        except re.error:
            return ToolResult(
                status="blocked",
                errorCode="grep_pattern_invalid",
                errorMessage="grep pattern invalid",
                metadata=_base_metadata(tool_name, reason="grep_pattern_invalid"),
            )
        return _blocked_result(tool_name, "local_readonly_tool_not_supported")

    def _file_read(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        root = _workspace_root(context)
        path_text = _string_arg(arguments, "path", "file", "filePath")
        if path_text is None:
            return _blocked_result("FileRead", "path_required")
        memory_block = _memory_mode_read_block("FileRead", path_text, context)
        if memory_block is not None:
            return memory_block

        if self._read_quality_enabled:
            missing = self._file_read_did_you_mean(root, path_text, context, arguments)
            if missing is not None:
                return missing

        resolved = _resolve_workspace_path(root, path_text, must_exist=True, require_file=True)
        max_bytes = _bounded_int(
            arguments.get("maxBytes"),
            default=_DEFAULT_MAX_READ_BYTES,
            minimum=1,
            maximum=_MAX_READ_BYTES_LIMIT,
        )
        raw = _read_bounded_bytes(resolved.path, max_bytes)
        truncated = len(raw) > max_bytes

        if self._read_quality_enabled:
            from magi_agent.coding.read_format import (
                LINE_NUMBER_GUIDANCE,
                apply_caps,
                binary_file_message,
                is_binary,
                number_lines,
            )

            if is_binary(raw[:max_bytes]):
                output = {
                    "path": resolved.relative,
                    "pathRef": resolved.path_ref,
                    "binary": True,
                    "content": binary_file_message(resolved.relative),
                    "truncated": False,
                    "digest": _digest(raw),
                }
                source_bundle = self._source_bundle(
                    context, "FileRead", ((resolved, ""),)
                )
                output["sourceRef"] = source_bundle.source_refs[0]
                return self._ok_result(
                    context,
                    "FileRead",
                    arguments,
                    output,
                    source_bundle=source_bundle,
                    redacted=False,
                    file_refs=source_bundle.source_refs,
                )

            limited = raw[:max_bytes].decode("utf-8", errors="replace")
            # Redaction MUST happen before numbering/caps.
            sanitized, redacted = _sanitize_text(limited)
            offset = _read_offset(arguments.get("offset"))
            limit = _read_limit(arguments.get("limit"))
            if offset > 1:
                sanitized = "\n".join(sanitized.split("\n")[offset - 1 :])
            capped, capped_truncated, next_offset = apply_caps(
                sanitized, max_lines=limit, max_bytes=max_bytes, offset=offset
            )
            footer = ""
            if capped_truncated and next_offset is not None:
                marker = (
                    f"\n(truncated at line {next_offset}; "
                    f"use offset={next_offset} to continue)"
                )
                if capped.endswith(marker):
                    capped = capped[: -len(marker)]
                footer = marker
            content = number_lines(capped, offset=offset) + footer
            truncated = truncated or capped_truncated
            source_bundle = self._source_bundle(
                context, "FileRead", ((resolved, content),)
            )
            output = {
                "sourceRef": source_bundle.source_refs[0],
                "path": resolved.relative,
                "pathRef": resolved.path_ref,
                "content": content,
                "truncated": truncated,
                "offset": offset,
                "digest": _digest(raw),
                "bytesRead": min(len(raw), max_bytes),
                "lineNumberGuidance": LINE_NUMBER_GUIDANCE,
            }
            if next_offset is not None:
                output["nextOffset"] = next_offset
            return self._ok_result(
                context,
                "FileRead",
                arguments,
                output,
                source_bundle=source_bundle,
                redacted=redacted,
                file_refs=source_bundle.source_refs,
            )

        limited = raw[:max_bytes].decode("utf-8", errors="replace")
        content, redacted = _sanitize_text(limited)
        source_bundle = self._source_bundle(
            context,
            "FileRead",
            ((resolved, content),),
        )
        output = {
            "sourceRef": source_bundle.source_refs[0],
            "path": resolved.relative,
            "pathRef": resolved.path_ref,
            "content": content,
            "truncated": truncated,
            "digest": _digest(raw),
            "bytesRead": min(len(raw), max_bytes),
        }
        return self._ok_result(
            context,
            "FileRead",
            arguments,
            output,
            source_bundle=source_bundle,
            redacted=redacted,
            file_refs=source_bundle.source_refs,
        )

    def _file_read_did_you_mean(
        self,
        root: Path,
        path_text: str,
        context: ToolContext,
        arguments: Mapping[str, object],
    ) -> ToolResult | None:
        """Return a did-you-mean ToolResult if the path is a clean miss.

        Only fires for paths that pass the workspace/secret policy but simply do
        not exist. Sealed/secret paths still raise their policy error so they are
        never suggested or revealed.
        """
        from magi_agent.coding.read_format import did_you_mean

        normalized = _normalize_relative(path_text)
        if not normalized:
            return None
        if _is_workspace_escape(path_text) or _is_sensitive_relative_path(normalized):
            return None
        candidate = root / normalized
        if candidate.exists():
            return None
        basename = normalized.rsplit("/", 1)[-1]
        parent_rel = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        parent_dir = root / parent_rel if parent_rel else root
        try:
            entries = [
                entry.name
                for entry in parent_dir.iterdir()
                if entry.is_file()
                and not entry.is_symlink()
                and not _is_sensitive_relative_path(
                    f"{parent_rel}/{entry.name}" if parent_rel else entry.name
                )
            ]
        except OSError:
            entries = []
        suggestions = did_you_mean(entries, basename)
        message = f"File not found: {normalized}"
        if suggestions:
            message = f"{message}. Did you mean? {', '.join(suggestions)}"
        return ToolResult(
            status="blocked",
            errorCode="path_not_found",
            errorMessage=message,
            metadata={
                **_base_metadata("FileRead", reason="path_not_found"),
                "fileNotFound": True,
                "suggestions": suggestions,
            },
        )

    def _glob(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        root = _workspace_root(context)
        pattern = _string_arg(arguments, "pattern", "glob") or "*"
        max_matches = _bounded_int(
            arguments.get("maxMatches"),
            default=_DEFAULT_MAX_MATCHES,
            minimum=1,
            maximum=_MAX_MATCH_LIMIT,
        )
        rg_files = _ripgrep_glob(root, pattern, max_files=max_matches + 1)
        if rg_files is not None:
            files = rg_files
        else:
            files = list(_safe_glob_files(root, pattern, max_files=max_matches + 1))
        files = _memory_mode_filter_readable_files(files, context)
        selected = files[:max_matches]
        source_bundle = self._source_bundle(
            context,
            "Glob",
            tuple((resolved, "") for resolved in selected),
        )
        matches = [
            {
                "path": resolved.relative,
                "pathRef": resolved.path_ref,
                "sourceRef": source_bundle.source_refs[index],
            }
            for index, resolved in enumerate(selected)
        ]
        output = {"matches": matches, "truncated": len(files) > len(selected)}
        return self._ok_result(
            context,
            "Glob",
            arguments,
            output,
            source_bundle=source_bundle,
            redacted=False,
            file_refs=source_bundle.source_refs,
        )

    def _grep(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        root = _workspace_root(context)
        pattern_text = _string_arg(arguments, "pattern", "query")
        if pattern_text is None:
            return _blocked_result("Grep", "grep_pattern_required")
        matcher = re.compile(pattern_text)
        glob_pattern = _string_arg(arguments, "glob", "path", "patternGlob") or "**/*"
        memory_block = _memory_mode_read_block("Grep", glob_pattern, context)
        if memory_block is not None:
            return memory_block
        max_files = _bounded_int(
            arguments.get("maxFiles"),
            default=_DEFAULT_MAX_FILES,
            minimum=1,
            maximum=_MAX_FILE_LIMIT,
        )
        max_matches = _bounded_int(
            arguments.get("maxMatches"),
            default=_DEFAULT_MAX_MATCHES,
            minimum=1,
            maximum=_MAX_MATCH_LIMIT,
        )
        max_bytes = _bounded_int(
            arguments.get("maxBytes"),
            default=_DEFAULT_MAX_READ_BYTES,
            minimum=1,
            maximum=_MAX_READ_BYTES_LIMIT,
        )
        rg_resolved = _ripgrep_grep_files(
            root, pattern_text, glob_pattern, max_files=max_files + 1
        )
        if rg_resolved is not None:
            files: tuple[_ResolvedPath, ...] = rg_resolved
        else:
            files = _safe_glob_files(root, glob_pattern, max_files=max_files + 1)
        files = _memory_mode_filter_readable_files(files, context)
        selected_files = files[:max_files]
        matches: list[dict[str, object]] = []
        source_inputs: list[tuple[_ResolvedPath, str]] = []
        redacted = False
        truncated_by_bytes = False
        for resolved in selected_files:
            raw = _read_bounded_bytes(resolved.path, max_bytes)
            truncated_by_bytes = truncated_by_bytes or len(raw) > max_bytes
            text = raw[:max_bytes].decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if matcher.search(line) is None:
                    continue
                snippet, snippet_redacted = _sanitize_text(
                    line[: _DEFAULT_MAX_SNIPPET_CHARS]
                )
                redacted = redacted or snippet_redacted
                source_index = _index_source_input(source_inputs, resolved, snippet)
                source_ref = f"src_{source_index + 1}"
                matches.append(
                    {
                        "sourceRef": source_ref,
                        "path": resolved.relative,
                        "pathRef": resolved.path_ref,
                        "line": line_number,
                        "snippet": snippet,
                    }
                )
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break
        source_bundle = self._source_bundle(context, "Grep", tuple(source_inputs))
        for index, match in enumerate(matches):
            if index < len(source_bundle.source_refs):
                match["sourceRef"] = source_bundle.source_refs[index]
        output = {
            "matches": matches,
            "fileCount": len(selected_files),
            "truncated": (
                len(files) > len(selected_files)
                or _grep_has_more(matches, max_matches)
                or truncated_by_bytes
            ),
        }
        return self._ok_result(
            context,
            "Grep",
            arguments,
            output,
            source_bundle=source_bundle,
            redacted=redacted,
            file_refs=source_bundle.source_refs,
        )

    def _git_diff(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        root = _workspace_root(context)
        diff_ref = _string_arg(arguments, "fixtureDiffRef", "diffRef")
        if diff_ref is None:
            return _blocked_result("GitDiff", "git_diff_fixture_required")
        diff_text = self._diff_fixtures.get(diff_ref)
        if diff_text is None:
            return _blocked_result("GitDiff", "git_diff_fixture_required")
        max_bytes = _bounded_int(
            arguments.get("maxBytes"),
            default=_DEFAULT_MAX_READ_BYTES,
            minimum=1,
            maximum=_MAX_READ_BYTES_LIMIT,
        )
        paths = _diff_paths(diff_text)
        resolved_paths = tuple(
            _resolve_workspace_path(root, path, must_exist=False, require_file=False)
            for path in paths
        )
        limited_text = diff_text[:max_bytes]
        preview, redacted = _sanitize_text(limited_text)
        source_bundle = self._source_bundle(
            context,
            "GitDiff",
            tuple((resolved, "") for resolved in resolved_paths),
        )
        files = [
            {
                "path": resolved.relative,
                "pathRef": resolved.path_ref,
                "sourceRef": source_bundle.source_refs[index],
            }
            for index, resolved in enumerate(resolved_paths)
        ]
        output = {
            "files": files,
            "preview": preview,
            "truncated": len(diff_text) > max_bytes,
            "subprocessFree": True,
            "digest": _digest(diff_text),
        }
        return self._ok_result(
            context,
            "GitDiff",
            arguments,
            output,
            source_bundle=source_bundle,
            redacted=redacted,
            file_refs=source_bundle.source_refs,
        )

    def _source_bundle(
        self,
        context: ToolContext,
        tool_name: str,
        sources: tuple[tuple[_ResolvedPath, str], ...],
    ) -> _SourceBundle:
        ledger = self._ledger(context)
        source_refs: list[str] = []
        receipts: list[dict[str, object]] = []
        # ``LocalResearchSourceLedger.record_source`` reads len(_records) then
        # appends (non-atomic), and the projection below iterates _records. When
        # readonly handlers are offloaded concurrently and share one (session,
        # turn) ledger, hold the host lock across the record + projection so
        # appends are not interleaved (no lost/duplicate source IDs) and the
        # projection reflects a consistent snapshot. This is all in-memory work —
        # the file I/O for these sources already completed before this point, so
        # the lock is never held across I/O.
        with self._state_lock:
            for resolved, snippet in sources:
                record = ledger.record_source(
                    {
                        "turnId": context.turn_id or "unknown-turn",
                        "toolName": tool_name,
                        "toolUseId": context.tool_use_id or f"{tool_name}:local",
                        "evidenceType": "SourceInspection",
                        "kind": "file",
                        "uri": f"workspace://{resolved.path_ref}",
                        "inspected": True,
                        "contentHash": _digest(resolved.path_ref + snippet),
                        "contentType": "text/plain",
                        "snippets": (snippet,) if snippet else (),
                        "metadata": {
                            "pathRef": resolved.path_ref,
                            "relativePathDigest": _digest(resolved.relative),
                        },
                    }
                )
                source_refs.append(record.source_id)
                receipts.append(
                    SourceEvidenceReceipt(
                        sourceRef=record.source_id,
                        openedAt=_timestamp(),
                        contentDigest=record.content_hash or _digest(resolved.path_ref),
                        snapshotRef=f"snapshot:{_short_digest(resolved.path_ref)}",
                        spanRef=f"span:{record.source_id}",
                        quoteDigest=_digest(snippet or resolved.path_ref),
                    ).public_projection()
                )
            projection = public_source_ledger_report(ledger).model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            )
        return _SourceBundle(
            source_refs=tuple(source_refs),
            projection=projection,
            receipts=tuple(receipts),
        )

    def _ledger(self, context: ToolContext) -> LocalResearchSourceLedger:
        key = (
            _public_context_ref(context.session_id, prefix="session"),
            _public_context_ref(context.turn_id, prefix="turn"),
        )
        # Atomic check-and-create under the lock: concurrent offloaded reads
        # sharing the same (session, turn) key must observe a single ledger
        # instance, otherwise a get-then-set race would create two ledgers and
        # silently drop one's recorded source records.
        with self._state_lock:
            ledger = self._ledgers.get(key)
            if ledger is None:
                ledger = LocalResearchSourceLedger(
                    ledgerId=f"ledger:{_short_digest(':'.join(key))}",
                    sessionId=key[0],
                    turnId=key[1],
                    agentRole=_agent_role(context, self._agent_role),
                )
                self._ledgers[key] = ledger
        return ledger

    def _ok_result(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: Mapping[str, object],
        output: Mapping[str, object],
        *,
        source_bundle: _SourceBundle,
        redacted: bool,
        file_refs: tuple[str, ...],
    ) -> ToolResult:
        receipt = ToolExecutionReceipt(
            receiptId=f"receipt:{_short_digest({'tool': tool_name, 'output': output})}",
            toolCallId=context.tool_use_id or f"tool-call:{_short_digest(tool_name)}",
            toolName=tool_name,
            toolVersion="local-readonly.v1",
            inputDigest=_digest(_sanitize_mapping(arguments)),
            outputDigest=_digest(output),
            status="success",
            startedAt=_timestamp(),
            endedAt=_timestamp(),
            authorityFlags=ReceiptAuthorityFlags(),
            policyDecisionId=f"policy:{_short_digest(tool_name)}",
            redactionStatus="redacted" if redacted else "no_redaction_needed",
            sourceRef=source_bundle.source_refs[0] if source_bundle.source_refs else None,
        ).public_projection()
        metadata = {
            **_base_metadata(tool_name, reason="tool_executed"),
            "localOnly": True,
            "sourceRefs": source_bundle.source_refs,
            "sourceProjection": source_bundle.projection,
            "sourceEvidenceReceipts": source_bundle.receipts,
            "toolExecutionReceipt": receipt,
        }
        return ToolResult(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "sourceRefs": source_bundle.source_refs,
            },
            fileRefs=file_refs,
            metadata=metadata,
        )


class _PathPolicyError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _workspace_root(context: ToolContext) -> Path:
    if not context.workspace_root:
        raise _PathPolicyError("workspace_root_required")
    root = Path(context.workspace_root).resolve()
    if not root.is_dir():
        raise _PathPolicyError("workspace_root_required")
    return root


def _resolve_workspace_path(
    root: Path,
    path_text: str,
    *,
    must_exist: bool,
    require_file: bool,
) -> _ResolvedPath:
    normalized = _normalize_relative(path_text)
    if not normalized:
        raise _PathPolicyError("path_required")
    if _is_workspace_escape(path_text):
        raise _PathPolicyError("path_escapes_workspace")
    if _is_sensitive_relative_path(normalized):
        raise _PathPolicyError("secret_path_denied")
    candidate = root / normalized
    _reject_symlink_components(root, normalized)
    if must_exist and not candidate.exists():
        raise _PathPolicyError("path_not_found")
    if candidate.is_symlink():
        raise _PathPolicyError("path_symlink_denied")
    if require_file and not candidate.is_file():
        raise _PathPolicyError("path_not_readable_file")
    if candidate.exists():
        resolved = candidate.resolve()
        if root not in (resolved, *resolved.parents):
            raise _PathPolicyError("path_symlink_escape_denied")
        if require_file and not resolved.is_file():
            raise _PathPolicyError("path_not_readable_file")
    return _ResolvedPath(
        path=candidate.resolve() if candidate.exists() else candidate,
        relative=normalized,
        path_ref=f"file:{_short_digest(normalized)}",
    )


def _safe_glob_files(root: Path, pattern: str, *, max_files: int) -> tuple[_ResolvedPath, ...]:
    normalized_pattern = _normalize_glob_pattern(pattern)
    if normalized_pattern is None:
        return ()
    matches: list[_ResolvedPath] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_dir = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames)
            if not (current_dir / dirname).is_symlink()
            and not _is_sensitive_relative_path(
                (current_dir / dirname).relative_to(root).as_posix()
            )
        ]
        for filename in sorted(filenames):
            candidate = current_dir / filename
            if candidate.is_symlink():
                continue
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            if _is_sensitive_relative_path(relative):
                continue
            if not _glob_pattern_matches(relative, normalized_pattern):
                continue
            try:
                resolved = _resolve_workspace_path(
                    root,
                    relative,
                    must_exist=True,
                    require_file=True,
                )
            except _PathPolicyError:
                continue
            matches.append(resolved)
            if len(matches) >= max_files:
                return tuple(matches)
    return tuple(matches)


def _ripgrep_active() -> bool:
    # NOTE: reads live os.environ via ripgrep_enabled() on every call — no
    # frozen config.  Contrast with Gate5BFullToolHost._ripgrep_active() which
    # reads self.config.ripgrep_enabled baked at construction time.  The two
    # are intentionally different: local_readonly is env-driven; gate5b is
    # config-driven.
    from magi_agent.config.env import ripgrep_enabled
    from magi_agent.coding.ripgrep import rg_available

    return ripgrep_enabled() and rg_available()


def _ripgrep_glob_arg(pattern: str) -> str | None:
    normalized = _normalize_glob_pattern(pattern)
    if normalized is None or normalized in {"*", "**", "**/*"}:
        return None
    return normalized


def _ripgrep_resolve(root: Path, raw: str) -> _ResolvedPath | None:
    """Validate an rg-reported path through the existing workspace policy."""

    normalized = _normalize_relative(raw)
    if not normalized or _is_workspace_escape(normalized):
        return None
    if _is_sensitive_relative_path(normalized):
        return None
    try:
        return _resolve_workspace_path(
            root, normalized, must_exist=True, require_file=True
        )
    except _PathPolicyError:
        return None


def _ripgrep_mtime_order(
    resolved: list[_ResolvedPath], *, max_files: int
) -> tuple[_ResolvedPath, ...]:
    """Stat each resolved path and return up to *max_files* sorted by mtime desc.

    Delegates to :func:`magi_agent.coding.ripgrep.mtime_sort` so the
    stat/OSError-swallow/tiebreak logic is shared with gate5b.
    rg over-fetches up to ~200 paths before this trims to max_files —
    acceptable overhead at current caps.
    """
    from magi_agent.coding.ripgrep import mtime_sort

    return tuple(
        mtime_sort(
            resolved,
            stat_path=lambda item: str(item.path),
            limit=max_files,
        )
    )


def _ripgrep_glob(
    root: Path, pattern: str, *, max_files: int
) -> tuple[_ResolvedPath, ...] | None:
    if not _ripgrep_active():
        return None
    from magi_agent.coding.ripgrep import rg_files

    glob = _ripgrep_glob_arg(pattern)
    raw = rg_files(str(root), glob, limit=max_files)
    seen: dict[str, _ResolvedPath] = {}
    for item in raw:
        resolved = _ripgrep_resolve(root, item)
        if resolved is not None and resolved.relative not in seen:
            seen[resolved.relative] = resolved
    return _ripgrep_mtime_order(list(seen.values()), max_files=max_files)


def _ripgrep_grep_files(
    root: Path, pattern: str, glob: str, *, max_files: int
) -> tuple[_ResolvedPath, ...] | None:
    if not _ripgrep_active():
        return None
    from magi_agent.coding.ripgrep import rg_search

    glob_arg = _ripgrep_glob_arg(glob)
    raw = rg_search(str(root), pattern, glob_arg, limit=max_files)
    seen: dict[str, _ResolvedPath] = {}
    for match in raw:
        if match.path in seen:
            continue
        resolved = _ripgrep_resolve(root, match.path)
        if resolved is not None:
            seen[match.path] = resolved
    return _ripgrep_mtime_order(list(seen.values()), max_files=max_files)


def _reject_symlink_components(root: Path, normalized: str) -> None:
    current = root
    for part in Path(normalized).parts:
        current = current / part
        if current.is_symlink():
            raise _PathPolicyError("path_symlink_denied")


def _read_bounded_bytes(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max_bytes + 1)


def _normalize_relative(path_text: str) -> str:
    text = str(path_text).strip().replace("\\", "/")
    normalized = posixpath.normpath(text)
    return "" if normalized == "." else normalized


def _normalize_glob_pattern(pattern: str) -> str | None:
    text = str(pattern or "*").strip().replace("\\", "/")
    if not text:
        return "*"
    if text.startswith(("/", "~")):
        return None
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts) or "*"


def _glob_pattern_matches(relative: str, pattern: str) -> bool:
    if pattern in {"**", "**/*"}:
        return True
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        return fnmatch.fnmatchcase(relative, suffix) or fnmatch.fnmatchcase(relative, pattern)
    if "/" not in pattern and "/" in relative:
        return False
    return fnmatch.fnmatchcase(relative, pattern)


def _is_workspace_escape(path_text: str) -> bool:
    if path_text.startswith(("/", "~")):
        return True
    normalized = _normalize_relative(path_text)
    slash_path = path_text.replace("\\", "/")
    return normalized == ".." or normalized.startswith("../") or "/../" in f"/{slash_path}/"


def _is_sensitive_relative_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").strip().lower()
    if normalized in _PROTECTED_RELATIVE_PATHS:
        return True
    if any(normalized.startswith(prefix) for prefix in _PROTECTED_RELATIVE_PREFIXES):
        return True
    parts = [part for part in normalized.split("/") if part]
    return any(
        part in {".", ".."} or _SENSITIVE_PATH_PART_RE.search(part) is not None
        for part in parts
    )


def _diff_paths(diff_text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw_line in diff_text.splitlines():
        line = raw_line.strip()
        if line.startswith("diff --git "):
            parts = line.split()
            for part in parts[2:4]:
                cleaned = _normalize_diff_path(part)
                if cleaned:
                    paths.append(cleaned)
        elif line.startswith("--- ") or line.startswith("+++ "):
            cleaned = _normalize_diff_path(line[4:].split("\t", 1)[0].strip())
            if cleaned:
                paths.append(cleaned)
    return tuple(dict.fromkeys(paths))


def _normalize_diff_path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    normalized = _normalize_relative(_DIFF_PATH_PREFIX_RE.sub("", value))
    return normalized or None


def _index_source_input(
    inputs: list[tuple[_ResolvedPath, str]],
    resolved: _ResolvedPath,
    snippet: str,
) -> int:
    inputs.append((resolved, snippet))
    return len(inputs) - 1


def _grep_has_more(matches: Sequence[object], max_matches: int) -> bool:
    return len(matches) >= max_matches


def _string_arg(arguments: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        value = arguments.get(name)
        if isinstance(value, str):
            return value
    return None


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(value, minimum), maximum)
    if isinstance(value, str) and value.isdecimal():
        return min(max(int(value), minimum), maximum)
    return default


def _read_offset(value: object) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else 1
    return 1


def _read_limit(value: object, default: int = 2000) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else default
    return default


def _sanitize_text(value: str) -> tuple[str, bool]:
    redacted = _SENSITIVE_TEXT_RE.sub("[redacted]", value)
    return redacted, redacted != value


def _sanitize_mapping(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, nested in value.items():
        key_text = str(key)
        if _SENSITIVE_PATH_PART_RE.search(key_text):
            safe[key_text] = "[redacted]"
        elif isinstance(nested, str):
            safe[key_text] = _sanitize_text(nested)[0]
        elif isinstance(nested, Mapping):
            safe[key_text] = _sanitize_mapping(nested)
        elif isinstance(nested, Sequence) and not isinstance(nested, bytes | bytearray | str):
            safe[key_text] = [
                _sanitize_text(item)[0] if isinstance(item, str) else item
                for item in nested
            ]
        else:
            safe[key_text] = nested
    return safe


def _base_metadata(tool_name: str, *, reason: str) -> dict[str, object]:
    return {
        "toolName": tool_name,
        "permissionClass": "read",
        "dangerous": False,
        "mutatesWorkspace": False,
        "reason": reason,
        "localOnly": True,
        "subprocessFree": True,
        "networkAllowed": False,
        "mutationAllowed": False,
    }


def _blocked_result(tool_name: str, reason: str) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=reason,
        errorMessage=reason.replace("_", " "),
        metadata=_base_metadata(tool_name, reason=reason),
    )


def _memory_mode_read_block(
    tool_name: str,
    path_text: str,
    context: ToolContext,
) -> ToolResult | None:
    """Block protected memory reads when the channel disables raw memory reads."""

    if not is_long_term_memory_read_disabled(context.memory_mode):
        return None
    if not is_protected_memory_path(path_text):
        return None
    reason = (
        "memory_mode_incognito"
        if is_incognito_memory_mode(context.memory_mode)
        else "memory_mode_blocked"
    )
    return ToolResult(
        status="blocked",
        errorCode=reason,
        errorMessage=protected_memory_error(path_text),
        metadata=_base_metadata(tool_name, reason=reason),
    )


def _memory_mode_filter_readable_files(
    files: Sequence[_ResolvedPath],
    context: ToolContext,
) -> tuple[_ResolvedPath, ...]:
    if not is_long_term_memory_read_disabled(context.memory_mode):
        return tuple(files)
    return tuple(
        resolved
        for resolved in files
        if not is_protected_memory_path(resolved.relative)
    )


def _digest(value: object) -> str:
    if isinstance(value, bytes):
        encoded = value
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=repr,
        ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _short_digest(value: object) -> str:
    return _digest(value).removeprefix("sha256:")[:_DIGEST_PREFIX_LENGTH]


def _public_context_ref(value: str | None, *, prefix: str) -> str:
    if not value:
        return f"{prefix}:local-readonly"
    return f"{prefix}:{_short_digest(value)}"


def _agent_role(context: ToolContext, default: str) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role", "sourceAgentRole", "source_agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return _coerce_agent_role(value)
    return default


def _coerce_agent_role(value: str) -> str:
    normalized = value.strip().casefold().replace("-", "_")
    if normalized in _AGENT_ROLES:
        return normalized
    return "general"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "LOCAL_READONLY_TOOL_NAMES",
    "LocalReadOnlyToolHost",
]
