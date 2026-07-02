"""rem2/F6 (deep-review N-22): transport/tool_preview -> shared/tool_preview.

``tool_preview`` (secret-token redaction + preview capping) is a pure ``re``
leaf that 17 cross-package modules import from the ``transport`` package,
creating avoidable back-edges into transport. It moves to the ``shared``
leaf with a ``sys.modules`` self-alias shim at the old path. The redaction
kernel is unchanged (byte-identical); the frozen-behavior test proves it.
"""

from __future__ import annotations

import subprocess
import sys


def _run_fresh_python(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_shared_tool_preview_module_exists() -> None:
    import magi_agent.shared.tool_preview as new

    assert new is not None


def test_old_and_new_paths_are_same_module() -> None:
    import magi_agent.shared.tool_preview as new
    import magi_agent.transport.tool_preview as old

    assert old is new


def test_redaction_behavior_frozen() -> None:
    from magi_agent.shared.tool_preview import (
        MAX_TOOL_PREVIEW,
        redact_secret_tokens,
        sanitize_tool_preview,
    )

    assert MAX_TOOL_PREVIEW == 400
    assert (
        redact_secret_tokens("Authorization: Bearer abc123")
        == "Authorization: Bearer [redacted]"
    )
    assert redact_secret_tokens("api_key = 'v'") == "api_key = '[redacted]'"
    # Runtime-assembled GitHub-token-shaped input (no contiguous secret literal).
    gh_token = "gh" + "p_" + "A" * 36
    assert redact_secret_tokens("token " + gh_token) == "token [redacted]"
    # Preview cap: a 401-char input is truncated to exactly 400 chars.
    capped = sanitize_tool_preview("x" * 401)
    assert len(capped) == 400
    assert capped == "x" * 397 + "..."
    assert (
        sanitize_tool_preview("Authorization: Bearer abc123")
        == "Authorization: Bearer [redacted]"
    )


def test_no_transport_machinery_loaded() -> None:
    completed = _run_fresh_python(
        """
import importlib
import sys

importlib.import_module("magi_agent.shared.tool_preview")
leaked = [m for m in sys.modules if m.startswith("magi_agent.transport.")]
assert leaked == [], leaked
"""
    )

    assert completed.returncode == 0, completed.stderr
