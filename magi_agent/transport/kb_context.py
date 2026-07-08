"""Local KB_CONTEXT turn resolver (self-host parity with chat-proxy).

The dashboard prepends a ``[KB_CONTEXT: <id>=<filename>, ...]`` marker to the
user message when files are attached (``apps/web/src/chat-core/kb-context-marker.ts``).
Hosted deployments resolve this in ``infra/docker/chat-proxy/kb-context.js``:
each ref's converted text is downloaded and inlined into the turn wrapped as
``<current-turn-source authority="L1">``, then the marker is stripped, so the
agent receives the document text inline.

The local runtime has no chat-proxy, so this module reproduces that contract
against the on-disk workspace KB. ``id`` is the workspace-relative path written
by ``POST /v1/app/knowledge/upload`` (e.g. ``knowledge/Downloads/report.pdf``).

Resolution strategy per ref:
  * text-like extensions -> direct UTF-8 read (fast, no ToolContext);
  * office/pdf formats    -> :func:`convert_file_to_markdown` (real extraction);
  * anything else / error -> a fail-soft note naming the file (never raises),
    so one bad attachment can't kill the turn.
"""

from __future__ import annotations

import re
from pathlib import Path

# Same prefix shape as kb-context-marker.ts KB_CONTEXT_RE.
_KB_CONTEXT_RE = re.compile(r"^\[KB_CONTEXT:\s*(.+?)\]\n?", re.DOTALL)

# Per-doc inlined-text budget. Mirrors the hosted 4 KiB..256 KiB bound.
_MIN_CHARS = 4_000
_MAX_CHARS = 262_144
_DEFAULT_CHARS = 65_536

# Read directly as UTF-8 text — no converter needed.
_TEXT_LIKE_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
        ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
        ".html", ".htm", ".svg", ".tex",
        ".py", ".pyw", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts",
        ".cts", ".c", ".h", ".cpp", ".cc", ".hpp", ".java", ".kt", ".swift",
        ".go", ".rs", ".rb", ".php", ".cs", ".sh", ".bash", ".zsh", ".sql",
        ".r", ".scala", ".dart", ".lua", ".pl", ".vue", ".svelte", ".css",
        ".scss", ".sass", ".less", ".graphql", ".proto", ".patch", ".diff",
    }
)

# Extract via the shared document converter (PDF/Office/archive).
# ``convert_file_to_markdown`` supports legacy ``.xls`` (via xls_read) as well as
# ``.xlsx`` — keep both here so an attached legacy Excel workbook is inlined
# instead of falling through to the unsupported-format note.
_CONVERTIBLE_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".zip"}
)


class KbContextRef:
    """A parsed ``id=filename`` pair from the marker."""

    __slots__ = ("ref_id", "filename")

    def __init__(self, ref_id: str, filename: str) -> None:
        self.ref_id = ref_id
        self.filename = filename


def _parse_refs(raw: str) -> list[KbContextRef]:
    refs: list[KbContextRef] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        eq = entry.find("=")
        if eq <= 0:
            continue
        refs.append(KbContextRef(entry[:eq].strip(), entry[eq + 1 :].strip()))
    return refs


def _clamp_chars(value: int) -> int:
    return max(_MIN_CHARS, min(_MAX_CHARS, value))


def _resolve_within(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``root``, refusing traversal escapes."""
    try:
        candidate = (root / rel.lstrip("/")).resolve()
        candidate.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return candidate


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _extract_text(
    path: Path,
    rel: str,
    *,
    workspace_root: Path,
    bot_id: str,
    session_id: str | None,
    turn_id: str | None,
    max_chars: int,
) -> tuple[str, bool] | None:
    """Return ``(text, truncated)`` or ``None`` when the format is unsupported."""
    suffix = path.suffix.casefold()
    if suffix in _TEXT_LIKE_EXTENSIONS:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return _truncate(raw, max_chars)
    if suffix in _CONVERTIBLE_EXTENSIONS:
        try:
            from magi_agent.tools.context import ToolContext  # noqa: PLC0415
            from magi_agent.tools.file_markdown import (  # noqa: PLC0415
                convert_file_to_markdown,
            )

            context = ToolContext(
                botId=bot_id,
                workspaceRoot=str(workspace_root),
                sessionId=session_id,
                turnId=turn_id,
            )
            result = convert_file_to_markdown(rel, context, max_chars=max_chars)
        except Exception:  # noqa: BLE001 — never let extraction kill the turn
            return None
        if getattr(result, "status", None) == "ok":
            return getattr(result, "markdown", "") or "", bool(
                getattr(result, "truncated", False)
            )
        return None
    return None


def _wrap_source(filename: str, text: str, *, chars: int, truncated: bool) -> str:
    lines = [
        f"[file: {filename or 'Untitled'}]",
        f"content_chars: {chars}",
        f"content_truncated: {str(bool(truncated)).lower()}",
        "---",
        text,
    ]
    body = "\n".join(lines)
    return f'<current-turn-source kind="knowledge" authority="L1">\n{body}\n</current-turn-source>'


def _wrap_note(filename: str, reason: str) -> str:
    body = f"[file: {filename or 'Untitled'} — could not be read: {reason}]"
    return f'<current-turn-source kind="knowledge" authority="L1">\n{body}\n</current-turn-source>'


def _resolve_one(
    ref: KbContextRef,
    *,
    workspace_root: Path,
    bot_id: str,
    session_id: str | None,
    turn_id: str | None,
    max_chars: int,
) -> str:
    target = _resolve_within(workspace_root, ref.ref_id)
    if target is None:
        return _wrap_note(ref.filename, "path outside workspace")
    if not target.is_file():
        return _wrap_note(ref.filename, "file not found")
    extracted = _extract_text(
        target,
        ref.ref_id,
        workspace_root=workspace_root,
        bot_id=bot_id,
        session_id=session_id,
        turn_id=turn_id,
        max_chars=max_chars,
    )
    if extracted is None:
        return _wrap_note(
            ref.filename,
            "unsupported format — open it with your document tools if needed",
        )
    text, truncated = extracted
    return _wrap_source(
        ref.filename, text, chars=len(text), truncated=truncated
    )


def apply_kb_context(
    prompt: str,
    *,
    workspace_root: Path,
    bot_id: str,
    session_id: str | None = None,
    turn_id: str | None = None,
    max_chars: int = _DEFAULT_CHARS,
) -> str:
    """Inline attached KB documents into the turn and strip the marker.

    Returns *prompt* unchanged when there is no ``[KB_CONTEXT: ...]`` prefix.
    """
    if not isinstance(prompt, str) or not prompt:
        return prompt
    match = _KB_CONTEXT_RE.match(prompt)
    if match is None:
        return prompt
    refs = _parse_refs(match.group(1))
    body = prompt[match.end() :]
    if not refs:
        return body
    budget = _clamp_chars(max_chars)
    parts = [
        _resolve_one(
            ref,
            workspace_root=workspace_root,
            bot_id=bot_id,
            session_id=session_id,
            turn_id=turn_id,
            max_chars=budget,
        )
        for ref in refs
    ]
    injected = "\n\n".join(parts)
    if body.strip():
        return f"{injected}\n\n{body}"
    return injected
