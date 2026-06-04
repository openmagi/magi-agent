from __future__ import annotations

import io
import json
import shutil
import time
from pathlib import Path

import pytest

import magi_agent.coding.lsp_client as lsp_client
from magi_agent.coding.lsp_client import (
    DEFAULT_DIAGNOSTIC_CAP,
    SEVERITY_ERROR,
    Diagnostic,
    LspClient,
    _ServerProcess,
    cap_diagnostics,
    collect_error_diagnostics,
    filter_error_diagnostics,
    format_diagnostic_line,
    format_diagnostics_block,
    language_id_for_path,
    redact_message,
)


def _err(line: int, col: int, message: str) -> Diagnostic:
    return Diagnostic(line=line, column=col, severity=SEVERITY_ERROR, message=message)


def test_language_id_for_path_covers_py_ts_js() -> None:
    assert language_id_for_path(Path("a.py")) == "python"
    assert language_id_for_path(Path("a.ts")) == "typescript"
    assert language_id_for_path(Path("a.js")) == "javascript"
    assert language_id_for_path(Path("a.md")) is None


def test_filter_keeps_only_error_severity() -> None:
    diagnostics = [
        _err(1, 1, "boom"),
        Diagnostic(line=2, column=1, severity=2, message="warn"),
        Diagnostic(line=3, column=1, severity=3, message="info"),
    ]
    kept = filter_error_diagnostics(diagnostics)
    assert [d.message for d in kept] == ["boom"]


def test_cap_limits_per_file() -> None:
    diagnostics = [_err(i, 1, f"e{i}") for i in range(1, 30)]
    assert len(cap_diagnostics(diagnostics, cap=20)) == 20
    assert len(cap_diagnostics(diagnostics, cap=5)) == 5
    assert DEFAULT_DIAGNOSTIC_CAP == 20


def test_format_line_and_block_shape() -> None:
    line = format_diagnostic_line(_err(4, 9, "name is not defined"))
    assert line == "ERROR [4:9] name is not defined"

    block = format_diagnostics_block("file-ref", [_err(1, 1, "a"), _err(2, 3, "b")])
    assert block.startswith('<diagnostics file="file-ref">')
    assert block.endswith("</diagnostics>")
    assert "ERROR [1:1] a" in block
    assert "ERROR [2:3] b" in block


def test_redaction_strips_private_paths_and_secrets() -> None:
    redacted = redact_message("error in /Users/kevin/secret token=abc123")
    assert "/Users/kevin" not in redacted
    assert "[redacted]" in redacted


def test_collect_is_fail_open_when_provider_raises() -> None:
    class _Boom:
        def diagnostics(self, path: Path, text: str) -> list[Diagnostic]:
            raise RuntimeError("server crashed")

    assert collect_error_diagnostics(_Boom(), Path("a.py"), "x") == []


def test_collect_filters_and_caps() -> None:
    class _Provider:
        def diagnostics(self, path: Path, text: str) -> list[Diagnostic]:
            return [
                _err(1, 1, "real error"),
                Diagnostic(line=2, column=1, severity=2, message="warning"),
                *[_err(i, 1, f"e{i}") for i in range(3, 40)],
            ]

    errors = collect_error_diagnostics(_Provider(), Path("a.py"), "x", cap=20)
    assert len(errors) == 20
    assert all(d.severity == SEVERITY_ERROR for d in errors)


def test_real_lsp_client_noop_when_server_missing(tmp_path: Path) -> None:
    # Unsupported language -> no-op regardless of installed servers.
    client = LspClient(tmp_path)
    assert client.diagnostics(tmp_path / "readme.md", "# hi") == []
    client.shutdown_all()


@pytest.mark.skipif(
    shutil.which("pyright-langserver") is None,
    reason="pyright-langserver not installed",
)
def test_real_pyright_reports_error(tmp_path: Path) -> None:  # pragma: no cover
    target = tmp_path / "broken.py"
    source = "x: int = 'not an int'\n"
    target.write_text(source, encoding="utf-8")
    with LspClient(tmp_path, timeout_s=15.0) as client:
        diagnostics = client.diagnostics(target, source)
    errors = filter_error_diagnostics(diagnostics)
    assert errors, "pyright should report at least one error"


# ---------------------------------------------------------------------------
# Deterministic JSON-RPC framing/handshake tests (fake stdio, no real server)
# ---------------------------------------------------------------------------


def _frame(message: dict[str, object]) -> bytes:
    """Render an LSP-framed message: Content-Length header + CRLF + body."""
    body = json.dumps(message).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class _FakeStream:
    """Readable byte stream supporting readline()/read(n) like a pipe.

    Backed by BytesIO but lets us inject a stall (no EOF, no data) so we can
    exercise the deadline-bounded body read without blocking the test.
    """

    def __init__(self, data: bytes, *, stall_after: int | None = None) -> None:
        self._buf = io.BytesIO(data)
        self._stall_after = stall_after
        self._read_count = 0

    def readline(self) -> bytes:
        return self._buf.readline()

    def read(self, size: int = -1) -> bytes:
        self._read_count += 1
        if self._stall_after is not None and self._read_count > self._stall_after:
            return b""  # simulate "no more bytes available right now"
        return self._buf.read(size)


class _FakeProc:
    """Minimal stand-in for subprocess.Popen with scripted stdout."""

    def __init__(self, stdout: object) -> None:
        self.stdout = stdout
        self.stdin = io.BytesIO()


def _server_with_stdout(stdout: object, *, timeout_s: float = 1.0) -> _ServerProcess:
    server = _ServerProcess(
        ("/bin/false",),
        Path("/tmp"),
        timeout_s=timeout_s,
        grace_s=0.05,
    )
    server._proc = _FakeProc(stdout)  # type: ignore[assignment]
    return server


def test_read_message_parses_well_formed_message() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    server = _server_with_stdout(_FakeStream(_frame(payload)))
    message = server._read_message(time.monotonic() + 5.0)
    assert message == payload


def test_read_message_handles_split_and_extra_headers() -> None:
    body = json.dumps({"id": 2, "result": None}).encode("utf-8")
    raw = (
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"\r\n"
        + body
    )
    server = _server_with_stdout(_FakeStream(raw))
    message = server._read_message(time.monotonic() + 5.0)
    assert message == {"id": 2, "result": None}


def test_read_message_missing_content_length_returns_none() -> None:
    raw = b"X-Foo: bar\r\n\r\n{}"
    server = _server_with_stdout(_FakeStream(raw))
    assert server._read_message(time.monotonic() + 5.0) is None


def test_read_message_eof_mid_header_returns_none() -> None:
    raw = b"Content-Length: 10\r\n"  # header started, never terminated, then EOF
    server = _server_with_stdout(_FakeStream(raw))
    assert server._read_message(time.monotonic() + 5.0) is None


def test_read_message_eof_mid_body_returns_none() -> None:
    body = json.dumps({"id": 3}).encode("utf-8")
    raw = (
        f"Content-Length: {len(body) + 50}\r\n\r\n".encode("ascii") + body
    )  # advertises more bytes than provided, then EOF
    server = _server_with_stdout(_FakeStream(raw))
    assert server._read_message(time.monotonic() + 5.0) is None


def test_read_message_body_read_honours_deadline_when_stalled() -> None:
    body = json.dumps({"id": 4}).encode("utf-8")
    raw = f"Content-Length: {len(body) + 100}\r\n\r\n".encode("ascii") + body
    # The first body read returns `body`; subsequent reads return b"" (stall),
    # which the loop treats as EOF -> None, without blocking forever.
    server = _server_with_stdout(_FakeStream(raw, stall_after=1))
    assert server._read_message(time.monotonic() + 5.0) is None


class _ScriptedDuplex:
    """A _ServerProcess wired to a scripted sequence of stdout messages.

    Each call to readline()/read() pulls from a flat byte buffer built by
    concatenating the framed messages, so _read_message drains them in order.
    """

    def __init__(self, messages: list[dict[str, object]]) -> None:
        raw = b"".join(_frame(m) for m in messages)
        self._buf = io.BytesIO(raw)

    def readline(self) -> bytes:
        return self._buf.readline()

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


def test_diagnostics_returns_populated_not_first_empty_publish() -> None:
    """Pyright emits an empty publishDiagnostics, then a populated one.

    The drain MUST NOT break on the first (empty) match — it must keep the
    populated payload. This is the fix for the silent-no-op bug.
    """
    uri = Path("/tmp/broken.py").resolve().as_uri()
    init_response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    empty_publish = {
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "diagnostics": []},
    }
    populated_publish = {
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {
            "uri": uri,
            "diagnostics": [
                {
                    "severity": 1,
                    "message": "x is not defined",
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                }
            ],
        },
    }
    server = _ServerProcess(
        ("/bin/false",), Path("/tmp"), timeout_s=2.0, grace_s=1.0
    )
    server._proc = _FakeProc(  # type: ignore[assignment]
        _ScriptedDuplex([init_response, empty_publish, populated_publish])
    )
    diagnostics = server.diagnostics(Path("/tmp/broken.py"), "x = y\n", "python")
    assert [d.message for d in diagnostics] == ["x is not defined"]
    assert diagnostics[0].line == 1  # 0-based LSP -> 1-based output
    assert diagnostics[0].column == 1


def test_diagnostics_clean_file_returns_empty_within_grace() -> None:
    """A clean file yields only an empty publish; we return [] after the grace
    window rather than blocking the full timeout."""
    uri = Path("/tmp/clean.py").resolve().as_uri()
    init_response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    empty_publish = {
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "diagnostics": []},
    }
    server = _ServerProcess(
        ("/bin/false",), Path("/tmp"), timeout_s=5.0, grace_s=0.1
    )
    server._proc = _FakeProc(  # type: ignore[assignment]
        _ScriptedDuplex([init_response, empty_publish])
    )
    started = time.monotonic()
    diagnostics = server.diagnostics(Path("/tmp/clean.py"), "value = 1\n", "python")
    elapsed = time.monotonic() - started
    assert diagnostics == []
    # Must not burn the full 5s timeout on a clean file.
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# Lifecycle: servers are torn down (shutdown + __del__ backstop)
# ---------------------------------------------------------------------------


class _RecordingServer:
    """Stand-in for _ServerProcess that records shutdown() calls."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.shutdown_calls = 0
        self.diagnostics_calls = 0

    def diagnostics(self, path: Path, text: str, language_id: str) -> list[Diagnostic]:
        self.diagnostics_calls += 1
        return []

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_client_shutdown_all_shuts_down_spawned_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[_RecordingServer] = []

    def _factory(*args: object, **kwargs: object) -> _RecordingServer:
        server = _RecordingServer()
        created.append(server)
        return server

    monkeypatch.setattr(lsp_client, "_ServerProcess", _factory)
    # Pretend pyright is installed so a server is created.
    monkeypatch.setattr(
        lsp_client, "_server_command", lambda _id: ("pyright-langserver", "--stdio")
    )

    client = LspClient(tmp_path)
    client.diagnostics(tmp_path / "a.py", "x = 1\n")
    assert len(created) == 1
    assert created[0].diagnostics_calls == 1
    assert created[0].shutdown_calls == 0

    client.shutdown_all()
    assert created[0].shutdown_calls == 1
    # Idempotent / no servers left.
    client.shutdown_all()
    assert created[0].shutdown_calls == 1


def test_server_process_del_backstop_calls_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    server = _ServerProcess(("/bin/false",), Path("/tmp"), timeout_s=1.0)

    original = server.shutdown

    def _tracking() -> None:
        calls.append(1)
        original()

    monkeypatch.setattr(server, "shutdown", _tracking)
    server.__del__()
    assert calls == [1]
