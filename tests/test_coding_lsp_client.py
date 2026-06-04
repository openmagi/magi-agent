from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from magi_agent.coding.lsp_client import (
    DEFAULT_DIAGNOSTIC_CAP,
    SEVERITY_ERROR,
    Diagnostic,
    LspClient,
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
