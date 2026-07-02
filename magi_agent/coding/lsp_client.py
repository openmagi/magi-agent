"""PR5 — Minimal LSP diagnostics client for after-edit self-correction.

OpenCode runs a language server after every edit and appends ERROR-severity
diagnostics to the tool output so the model self-corrects in the same turn.
This module provides the minimal, robust pieces magi-agent needs to do the
same:

* A pure :class:`Diagnostic` value type plus deterministic formatting/filtering
  helpers (severity filter, per-file cap, ``<diagnostics file="...">`` block).
* A :class:`DiagnosticsProvider` protocol — the seam that the gate5b
  integration path depends on. Tests inject a fake provider so the integration
  test never needs a real language server installed.
* A real, minimal stdio JSON-RPC :class:`LspClient` (pyright for ``.py``,
  typescript-language-server for ``.ts``/``.js``) behind that seam. It is
  lazy (servers start per workspace on first use), bounded by a timeout, and
  fail-open: a missing server, a handshake failure, or a timeout yields *no*
  diagnostics rather than raising.

All raw text routed toward evidence must stay public-safe; see
``redact_message`` and the redaction the gate5b caller applies before evidence
construction.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


# LSP DiagnosticSeverity enum values (LSP spec): 1=Error, 2=Warning, 3=Info, 4=Hint.
SEVERITY_ERROR = 1

# OpenCode appends only ERROR-severity diagnostics, capped per file (~20).
DEFAULT_DIAGNOSTIC_CAP = 20

# Bound how long we wait for a language server to publish diagnostics.
DEFAULT_DIAGNOSTICS_TIMEOUT_S = 5.0

# After the first publishDiagnostics arrives for a URI, how long to wait for a
# follow-up (populated) publish before returning. Keeps clean-file writes from
# burning the whole timeout while still catching pyright's empty-then-populated
# sequence. Always clamped to the overall timeout.
DEFAULT_DIAGNOSTICS_GRACE_S = 1.5

_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

# Language-server commands keyed by language family. Detected via shutil.which.
# NOTE: the spec's MAGI_LSP_SERVERS (ext->cmd) override is intentionally NOT
# implemented — an env-driven command map is an arbitrary-process-spawn
# injection surface in a multi-tenant fleet. The hardcoded table below is the
# allowlist; add languages here in code, not via env.
_SERVER_COMMANDS: dict[str, tuple[str, ...]] = {
    "python": ("pyright-langserver", "--stdio"),
    "typescript": ("typescript-language-server", "--stdio"),
    "javascript": ("typescript-language-server", "--stdio"),
}
_LANGUAGE_FAMILY: dict[str, str] = {
    "python": "python",
    "typescript": "typescript",
    "typescriptreact": "typescript",
    "javascript": "javascript",
    "javascriptreact": "javascript",
}

# Redaction for diagnostic message text before it can flow toward evidence.
# Token shapes (sk-, gh[opusr]_, github_pat_, xox*, AKIA, AIza) are kept in
# sync with the shared gate transcript pattern
# gates._redaction_common.SENSITIVE_TRANSCRIPT_RE so the model-facing redaction
# is no weaker than the evidence layer's.
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?"
    r"|/home(?:/[^\s,;}\"']*)?"
    r"|/workspace(?:/[^\s,;}\"']*)?"
    r"|/data/bots(?:/[^\s,;}\"']*)?"
    r"|/var/lib(?:/[^\s,;}\"']*)?"
    r"|/private/var(?:/[^\s,;}\"']*)?"
    r"|\bbearer\s+\S+"
    r"|authorization\s*:"
    r"|\bsk-[A-Za-z0-9._-]+"
    r"|gh[opusr]_[A-Za-z0-9_]+"
    r"|github_pat_[A-Za-z0-9_]+"
    r"|xox[a-z]-[A-Za-z0-9._-]+"
    r"|AKIA[0-9A-Z]{8,}"
    r"|AIza[A-Za-z0-9_-]+"
    r"|\bcookie\b"
    r"|\btoken\b"
    r"|\bsecret\b"
    r"|\bpassword\b"
    r"|\bcredential\b"
    r"|private[_-]?key"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Diagnostic:
    """A single language-server diagnostic.

    Lines/columns are 1-based in the rendered output (LSP is 0-based; the
    parser normalizes when reading ``publishDiagnostics``).
    """

    line: int
    column: int
    severity: int
    message: str


class DiagnosticsProvider(Protocol):
    """Seam between the gate5b write path and the real LSP client.

    Implementations return the *raw* diagnostics for a file. Filtering, capping
    and formatting are applied by the caller via the pure helpers below so the
    behaviour is identical regardless of provider.
    """

    def diagnostics(self, path: Path, text: str) -> list[Diagnostic]:
        ...


# ---------------------------------------------------------------------------
# Pure helpers (deterministic; unit-tested without any language server)
# ---------------------------------------------------------------------------


def language_id_for_path(path: Path) -> str | None:
    """Return the LSP languageId for *path*, or ``None`` if unsupported."""
    return _LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def filter_error_diagnostics(diagnostics: Sequence[Diagnostic]) -> list[Diagnostic]:
    """Keep only ERROR-severity diagnostics (mirrors OpenCode behaviour)."""
    return [item for item in diagnostics if item.severity == SEVERITY_ERROR]


def cap_diagnostics(
    diagnostics: Sequence[Diagnostic],
    *,
    cap: int = DEFAULT_DIAGNOSTIC_CAP,
) -> list[Diagnostic]:
    """Return at most *cap* diagnostics (per-file cap)."""
    if cap < 0:
        cap = 0
    return list(diagnostics[:cap])


def redact_message(message: str) -> str:
    """Strip private paths/secrets and collapse whitespace from a message."""
    collapsed = " ".join(message.split())
    return _PRIVATE_TEXT_RE.sub("[redacted]", collapsed)


def format_diagnostic_line(diagnostic: Diagnostic) -> str:
    """Render a single ``ERROR [line:col] message`` line (redaction-safe)."""
    return (
        f"ERROR [{diagnostic.line}:{diagnostic.column}] "
        f"{redact_message(diagnostic.message)}"
    )


def format_diagnostics_block(
    file_label: str,
    diagnostics: Sequence[Diagnostic],
) -> str:
    """Render the ``<diagnostics file="...">...</diagnostics>`` block.

    *file_label* must already be a public-safe label (e.g. a relative path or a
    digest); this function does not leak it through the private-text filter
    because the caller controls it, but it is still redacted defensively.
    """
    safe_label = redact_message(file_label)
    lines = [format_diagnostic_line(item) for item in diagnostics]
    body = "\n".join(lines)
    return f'<diagnostics file="{safe_label}">\n{body}\n</diagnostics>'


def collect_error_diagnostics(
    provider: DiagnosticsProvider,
    path: Path,
    text: str,
    *,
    cap: int = DEFAULT_DIAGNOSTIC_CAP,
) -> list[Diagnostic]:
    """Run *provider*, then ERROR-filter and cap. Fail-open on any error."""
    try:
        raw = provider.diagnostics(path, text)
    except Exception:  # noqa: BLE001 — fail-open: never break a write
        return []
    return cap_diagnostics(filter_error_diagnostics(raw), cap=cap)


# ---------------------------------------------------------------------------
# Real stdio JSON-RPC LSP client
# ---------------------------------------------------------------------------


def _server_command(language_id: str) -> tuple[str, ...] | None:
    family = _LANGUAGE_FAMILY.get(language_id)
    if family is None:
        return None
    command = _SERVER_COMMANDS.get(family)
    if command is None:
        return None
    resolved = shutil.which(command[0])
    if resolved is None:
        return None
    return (resolved, *command[1:])


def _path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


class _ServerProcess:
    """A single language-server subprocess speaking LSP over stdio.

    Minimal and robust: one initialize handshake, one open document at a time,
    diagnostics collected from ``publishDiagnostics`` notifications. All I/O is
    bounded by a deadline; on any failure the process is terminated and the
    caller gets no diagnostics.
    """

    def __init__(
        self,
        command: Sequence[str],
        workspace_root: Path,
        *,
        timeout_s: float,
        grace_s: float = DEFAULT_DIAGNOSTICS_GRACE_S,
    ) -> None:
        self._command = tuple(command)
        self._workspace_root = workspace_root.resolve()
        self._timeout_s = timeout_s
        self._grace_s = grace_s
        self._proc: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._initialized = False
        self._lock = threading.Lock()

    def _start(self) -> bool:
        try:
            self._proc = subprocess.Popen(  # noqa: S603 — command is which-resolved
                self._command,
                cwd=str(self._workspace_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
        except (OSError, ValueError):
            self._proc = None
            return False
        return self._proc.stdin is not None and self._proc.stdout is not None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, payload: dict[str, object]) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            proc.stdin.write(header + body)
            proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def _read_message(self, deadline: float) -> dict[str, object] | None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        stream = proc.stdout
        content_length: int | None = None
        # Read headers.
        while True:
            if time.monotonic() > deadline:
                return None
            line = stream.readline()
            if line == b"":
                return None
            stripped = line.strip()
            if stripped == b"":
                break
            if stripped.lower().startswith(b"content-length:"):
                try:
                    content_length = int(stripped.split(b":", 1)[1].strip())
                except ValueError:
                    return None
        if content_length is None:
            return None
        # Read the body in a loop so the whole read honours the deadline. A
        # single ``stream.read(content_length)`` can block forever if the
        # server sends a header then stalls mid-body.
        chunks: list[bytes] = []
        remaining = content_length
        while remaining > 0:
            if time.monotonic() > deadline:
                return None
            chunk = stream.read(remaining)
            if not chunk:
                return None  # EOF mid-body
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) < content_length:
            return None
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None

    def _initialize(self, deadline: float) -> bool:
        request_id = self._next_id()
        ok = self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "processId": os.getpid(),
                    "rootUri": _path_to_uri(self._workspace_root),
                    "capabilities": {
                        "textDocument": {
                            "publishDiagnostics": {"relatedInformation": False},
                        }
                    },
                    "workspaceFolders": None,
                },
            }
        )
        if not ok:
            return False
        # Drain until the initialize response arrives.
        while True:
            message = self._read_message(deadline)
            if message is None:
                return False
            if message.get("id") == request_id:
                break
        return self._send(
            {"jsonrpc": "2.0", "method": "initialized", "params": {}}
        )

    def diagnostics(self, path: Path, text: str, language_id: str) -> list[Diagnostic]:
        with self._lock:
            return self._diagnostics_locked(path, text, language_id)

    def _diagnostics_locked(
        self,
        path: Path,
        text: str,
        language_id: str,
    ) -> list[Diagnostic]:
        deadline = time.monotonic() + self._timeout_s
        if self._proc is None:
            if not self._start():
                return []
        if not self._initialized:
            if not self._initialize(deadline):
                self.shutdown()
                return []
            self._initialized = True

        uri = _path_to_uri(path)
        opened = self._send(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": self._next_id(),
                        "text": text,
                    }
                },
            }
        )
        if not opened:
            self.shutdown()
            return []

        # Drain publishDiagnostics for this URI. Pyright (and others) emit an
        # initial EMPTY publishDiagnostics right after didOpen, then a populated
        # one once analysis completes — so we must NOT break on the first match
        # or we'd return [] and silently miss real errors. diagnostics are a
        # full replacement per publish, so we keep the LAST matching payload.
        #
        # Clean-file tradeoff: a file with no errors only ever produces empty
        # publishes, so we can't distinguish "clean" from "still analysing"
        # purely from content. To avoid hanging the full timeout on every clean
        # write, after the first matching publish arrives we wait only a short
        # grace window for a follow-up populated publish; if none arrives we
        # return what we have ([] for clean files). A populated publish returns
        # immediately. The hard deadline still bounds the worst case.
        grace_s = min(self._grace_s, self._timeout_s)
        diagnostics: list[Diagnostic] = []
        saw_publish = False
        while True:
            effective_deadline = deadline
            if saw_publish:
                effective_deadline = min(deadline, time.monotonic() + grace_s)
            message = self._read_message(effective_deadline)
            if message is None:
                break
            if (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
            ):
                params = message["params"]
                if params.get("uri") == uri:
                    diagnostics = _parse_publish_diagnostics(params)
                    saw_publish = True
                    if diagnostics:
                        # A populated payload is what we want; return promptly.
                        break
        # Close the document so the next open is clean.
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didClose",
                "params": {"textDocument": {"uri": uri}},
            }
        )
        return diagnostics

    def shutdown(self) -> None:
        proc = self._proc
        self._proc = None
        self._initialized = False
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            try:
                proc.kill()
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass

    def __del__(self) -> None:
        # Defense-in-depth: if an owner forgets to call shutdown(), reap the
        # subprocess on GC so we don't leak it for the worker lifetime.
        try:
            self.shutdown()
        except Exception:  # noqa: BLE001 — never raise from a finalizer
            pass


def _parse_publish_diagnostics(params: dict[str, object]) -> list[Diagnostic]:
    raw = params.get("diagnostics")
    if not isinstance(raw, list):
        return []
    parsed: list[Diagnostic] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        if not isinstance(severity, int):
            severity = SEVERITY_ERROR
        message = item.get("message")
        if not isinstance(message, str):
            continue
        rng = item.get("range")
        line = 1
        column = 1
        if isinstance(rng, dict):
            start = rng.get("start")
            if isinstance(start, dict):
                line = int(start.get("line", 0)) + 1
                column = int(start.get("character", 0)) + 1
        parsed.append(
            Diagnostic(
                line=line,
                column=column,
                severity=severity,
                message=message,
            )
        )
    return parsed


class LspClient:
    """Lazy, per-workspace real LSP client. Fail-open by construction.

    One language-server subprocess is started per (workspace, language family)
    on first use and reused. Servers are torn down via :meth:`shutdown_all`.
    If the relevant server binary is not installed, ``diagnostics`` returns an
    empty list (no-op) and no process is started.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        timeout_s: float = DEFAULT_DIAGNOSTICS_TIMEOUT_S,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._timeout_s = timeout_s
        self._servers: dict[str, _ServerProcess] = {}
        self._lock = threading.Lock()

    def diagnostics(self, path: Path, text: str) -> list[Diagnostic]:
        language_id = language_id_for_path(path)
        if language_id is None:
            return []
        family = _LANGUAGE_FAMILY.get(language_id)
        if family is None:
            return []
        command = _server_command(language_id)
        if command is None:
            return []  # server not installed -> no-op / fail-open
        with self._lock:
            server = self._servers.get(family)
            if server is None:
                server = _ServerProcess(
                    command,
                    self._workspace_root,
                    timeout_s=self._timeout_s,
                )
                self._servers[family] = server
        try:
            return server.diagnostics(path, text, language_id)
        except Exception:  # noqa: BLE001 — fail-open
            return []

    def shutdown_all(self) -> None:
        with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
        for server in servers:
            server.shutdown()

    def __enter__(self) -> LspClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown_all()

    def __del__(self) -> None:
        # Defense-in-depth backstop: reap any spawned servers on GC if the
        # owner forgot to call shutdown_all()/__exit__.
        try:
            self.shutdown_all()
        except Exception:  # noqa: BLE001 — never raise from a finalizer
            pass


__all__ = [
    "DEFAULT_DIAGNOSTIC_CAP",
    "DEFAULT_DIAGNOSTICS_TIMEOUT_S",
    "SEVERITY_ERROR",
    "Diagnostic",
    "DiagnosticsProvider",
    "LspClient",
    "cap_diagnostics",
    "collect_error_diagnostics",
    "filter_error_diagnostics",
    "format_diagnostic_line",
    "format_diagnostics_block",
    "language_id_for_path",
    "redact_message",
]
