"""Cold-start import discipline tests for CLI Stream F (PR-F1).

These tests use subprocess to run real Python processes and assert that the
heavy libraries (textual, google.adk) are NOT imported on cold paths:
  - the --version fast path
  - the headless path (no TUI import)

They also verify that importing cli.wiring itself does NOT pull textual/rich.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# Use the running venv python (same interpreter that runs the tests).
VENV_PYTHON = sys.executable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_snippet(snippet: str, *, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run a Python snippet in a subprocess, returning stdout + stderr."""
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [VENV_PYTHON, "-c", textwrap.dedent(snippet)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


# ---------------------------------------------------------------------------
# --version cold-start: no textual, no google.adk, no typer
# ---------------------------------------------------------------------------

def test_version_path_does_not_import_textual_or_adk() -> None:
    """Running the --version fast path must NOT import textual, google.adk, or typer."""
    # Separate the version-print from the module-check: we call _get_version()
    # directly (which hits the sys.exit(0) path), then in a second run we check
    # the modules after _get_version runs without calling sys.exit.
    # Use two snippets: one for the version output, one for the module leak check.
    version_snippet = """
        import sys
        sys.argv = ['magi', '--version']
        # Call only _get_version to print the version without sys.exit
        from openmagi_core_agent.cli.__main__ import _get_version
        print(_get_version())
        # Now check: did importing __main__ already pull in heavy deps?
        heavy = ['textual', 'typer', 'google.adk', 'google_adk']
        leaked = [m for m in heavy if any(k.startswith(m) for k in sys.modules)]
        print('leaked:', leaked)
    """
    result = _run_snippet(version_snippet)
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\\nstderr: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    # First line should be the version string.
    assert lines, f"Expected version output, got: {result.stdout!r}"
    version_line = lines[0].strip()
    assert version_line, f"Empty version line: {result.stdout!r}"
    # Second+ lines: leaked check
    assert "leaked: []" in result.stdout, f"Heavy modules were imported:\\n{result.stdout}"


def test_version_output_is_non_empty_string() -> None:
    """--version path prints a non-empty string and exits 0."""
    snippet = """
        import sys
        sys.argv = ['magi', '--version']
        from openmagi_core_agent.cli.__main__ import main
        main()
    """
    result = _run_snippet(snippet)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip(), "Expected non-empty version string"


# ---------------------------------------------------------------------------
# Headless path: importing cli.wiring must NOT pull textual
# ---------------------------------------------------------------------------

def test_import_cli_wiring_does_not_import_textual() -> None:
    """Importing cli.wiring and calling build_headless_runtime must not load textual."""
    snippet = """
        import sys
        # Import wiring and run the headless builder.
        from openmagi_core_agent.cli.wiring import build_headless_runtime
        rt = build_headless_runtime(cwd='/tmp', session_id='test-session')
        # Check textual / rich did not leak.
        leaked = [m for m in sys.modules if m == 'textual' or m.startswith('textual.')]
        print('textual_leaked:', bool(leaked))
        leaked_rich = [m for m in sys.modules if m == 'rich' or m.startswith('rich.')]
        print('rich_leaked:', bool(leaked_rich))
    """
    result = _run_snippet(snippet, env_extra={"MAGI_CLI_ENABLED": "1"})
    assert result.returncode == 0, f"snippet failed:\\nstderr: {result.stderr}"
    assert "textual_leaked: False" in result.stdout, f"textual was imported:\\n{result.stdout}"
    assert "rich_leaked: False" in result.stdout, f"rich was imported:\\n{result.stdout}"


def test_headless_path_does_not_import_textual() -> None:
    """Running a headless turn with an injected stub driver must not load textual."""
    snippet = """
        import sys, asyncio, os
        os.environ['MAGI_CLI_ENABLED'] = '1'
        from openmagi_core_agent.cli.wiring import build_headless_runtime
        from openmagi_core_agent.cli.headless import run_headless, StubEngineDriver
        import io
        rt = build_headless_runtime(cwd='/tmp', session_id='headless-cold')
        buf = io.StringIO()
        code = asyncio.run(run_headless(
            'hello',
            output='text',
            driver=StubEngineDriver(text='ok'),
            gate=rt.gate,
            commands=rt.commands,
            session_id=rt.session_log.path.stem if hasattr(rt.session_log, 'path') else 'sid',
            stream=buf,
        ))
        leaked = [m for m in sys.modules if m == 'textual' or m.startswith('textual.')]
        print('textual_leaked:', bool(leaked))
        print('exit_code:', code)
    """
    result = _run_snippet(snippet, env_extra={"MAGI_CLI_ENABLED": "1"})
    assert result.returncode == 0, f"snippet failed:\\nstderr: {result.stderr}"
    assert "textual_leaked: False" in result.stdout, f"textual was imported:\\n{result.stdout}"
    assert "exit_code: 0" in result.stdout, f"Expected exit 0, got:\\n{result.stdout}"


# ---------------------------------------------------------------------------
# build_headless_runtime returns correct types
# ---------------------------------------------------------------------------

def test_build_headless_runtime_returns_correct_types() -> None:
    """build_headless_runtime returns a named tuple/dataclass with the right types."""
    from openmagi_core_agent.cli.wiring import build_headless_runtime
    from openmagi_core_agent.cli.engine import MagiEngineDriver
    from openmagi_core_agent.cli.permissions import RulesPermissionGate
    from openmagi_core_agent.cli.session_log import SessionLog
    from openmagi_core_agent.cli.contracts import CommandRegistry

    rt = build_headless_runtime(cwd="/tmp", session_id="test-types")

    assert isinstance(rt.engine, MagiEngineDriver), f"Expected MagiEngineDriver, got {type(rt.engine)}"
    assert isinstance(rt.gate, RulesPermissionGate), f"Expected RulesPermissionGate, got {type(rt.gate)}"
    assert isinstance(rt.session_log, SessionLog), f"Expected SessionLog, got {type(rt.session_log)}"
    # Commands must satisfy the CommandRegistry protocol.
    assert isinstance(rt.commands, CommandRegistry), f"Expected CommandRegistry, got {type(rt.commands)}"


def test_build_headless_runtime_no_textual_after_import(monkeypatch) -> None:
    """After calling build_headless_runtime, textual must not appear in sys.modules."""
    # Remove textual from modules if somehow already present (defensive).
    import sys
    for key in list(sys.modules.keys()):
        if key == "textual" or key.startswith("textual."):
            del sys.modules[key]

    from openmagi_core_agent.cli.wiring import build_headless_runtime
    build_headless_runtime(cwd="/tmp", session_id="no-textual")

    leaked = [m for m in sys.modules if m == "textual" or m.startswith("textual.")]
    assert not leaked, f"textual was imported by build_headless_runtime: {leaked}"


def test_cli_wiring_with_composio_env_missing_package_does_not_import_sdk_or_mcp_toolset() -> None:
    snippet = """
        import builtins
        import sys
        import tempfile
        from pathlib import Path

        original_import = builtins.__import__

        def blocked_composio_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'composio' or name.startswith('composio.'):
                raise ImportError("No module named 'composio'")
            return original_import(name, globals, locals, fromlist, level)

        fake_root = Path(tempfile.mkdtemp())
        fake_mcp = fake_root / 'google' / 'adk' / 'tools' / 'mcp_tool'
        fake_mcp.mkdir(parents=True)
        for package in [
            fake_root / 'google',
            fake_root / 'google' / 'adk',
            fake_root / 'google' / 'adk' / 'tools',
            fake_mcp,
        ]:
            (package / '__init__.py').write_text('', encoding='utf-8')
        (fake_mcp / 'mcp_toolset.py').write_text(
            'class McpToolset:\\n'
            '    def __init__(self, **kwargs):\\n'
            '        self.kwargs = kwargs\\n'
            'class StreamableHTTPConnectionParams:\\n'
            '    def __init__(self, **kwargs):\\n'
            '        self.kwargs = kwargs\\n',
            encoding='utf-8',
        )
        sys.path.insert(0, str(fake_root))

        builtins.__import__ = blocked_composio_import
        try:
            from openmagi_core_agent.cli.wiring import build_headless_runtime

            rt = build_headless_runtime(cwd='/tmp', session_id='cold-composio')
        finally:
            builtins.__import__ = original_import

        print('status:', rt.composio.status)
        print('mcp_servers:', rt.mcp_servers)
        import sys
        leaked = [m for m in sys.modules if m == 'composio' or m.startswith('composio.')]
        mcp = [m for m in sys.modules if m == 'google.adk.tools.mcp_tool' or m.startswith('google.adk.tools.mcp_tool.')]
        print('composio_leaked:', bool(leaked))
        print('mcp_leaked:', bool(mcp))
    """
    result = _run_snippet(
        snippet,
        env_extra={
            "COMPOSIO_API_KEY": "cp_test_secret",
            "MAGI_COMPOSIO_ENABLED": "on",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "status: missing_package" in result.stdout
    assert "mcp_servers: ()" in result.stdout
    assert "composio_leaked: False" in result.stdout
    assert "mcp_leaked: False" in result.stdout
