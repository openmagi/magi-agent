"""File handlers should accept the common ``filePath`` alias for ``path``.

Defense-in-depth: even with an explicit schema, some models emit ``filePath``.
The handler must treat it as ``path`` rather than denying an empty path.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)


def _host(workspace: Path) -> Gate5BFullToolHost:
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "environment": "local",
            "environmentAllowlist": ("local",),
            "allowedToolNames": ("FileWrite", "FileRead"),
            "maxToolCallsPerTurn": 8,
            "maxPerToolOutputBytes": 8192,
            "commandTimeoutMs": 5000,
        }
    )
    return Gate5BFullToolHost(
        config=config,
        workspace_root=workspace,
        exposed_tool_names=("FileWrite", "FileRead"),
        now_ms=lambda: 0,
    )


def test_file_write_accepts_filepath_alias(tmp_path: Path):
    host = _host(tmp_path)

    async def run():
        return await host.dispatch(
            "FileWrite",
            {"filePath": "out.txt", "content": "hello"},
            request_digest="sha256:test",
            tool_call_id="t1",
        )

    outcome = asyncio.run(run())
    assert outcome.status == "ok", getattr(outcome, "reason", outcome.status)
    assert (tmp_path / "out.txt").read_text() == "hello"
