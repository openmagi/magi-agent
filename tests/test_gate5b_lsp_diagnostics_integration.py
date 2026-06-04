from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from magi_agent.coding.lsp_client import SEVERITY_ERROR, Diagnostic
from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class _FakeProvider:
    """Deterministic diagnostics provider: errors only for files containing
    the literal token ``raise_error`` so the integration test never needs a
    real language server."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def diagnostics(self, path: Path, text: str) -> list[Diagnostic]:
        self.calls.append(path)
        if "raise_error" in text:
            return [
                Diagnostic(line=1, column=1, severity=SEVERITY_ERROR, message="boom"),
                Diagnostic(line=2, column=5, severity=2, message="just a warning"),
            ]
        return []


def _config(*, lsp_enabled: bool) -> Gate5BFullToolHostConfig:
    return Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
            "lspDiagnosticsEnabled": lsp_enabled,
        }
    )


def _scope() -> dict[str, object]:
    return {
        "selectedBotDigest": _sha256("bot-test"),
        "selectedOwnerDigest": _sha256("user-test"),
        "environment": "production",
    }


@pytest.mark.asyncio
async def test_enabled_with_error_appends_block_and_evidence(tmp_path: Path) -> None:
    provider = _FakeProvider()
    bundle = build_gate5b_full_toolhost_bundle(
        config=_config(lsp_enabled=True),
        scope=_scope(),
        workspace_root=tmp_path,
        diagnostics_provider=provider,
    )
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "broken.py", "content": "raise_error = undefined_name\n"},
        request_digest=_sha256("req-1"),
        tool_call_id="call-1",
    )
    assert outcome.status == "ok"
    assert provider.calls, "diagnostics provider should run on a .py write"

    preview = outcome.output_preview
    assert isinstance(preview, dict)
    block = preview.get("lspDiagnostics")
    assert isinstance(block, str)
    assert "LSP errors detected in this file, please fix:" in block
    assert "<diagnostics file=" in block
    assert "ERROR [1:1] boom" in block
    # WARNING severity must be filtered out.
    assert "just a warning" not in block

    record = outcome.code_diagnostics_receipt
    assert record is not None
    assert record.type == "CodeDiagnostics"
    assert record.error_count == 1  # only ERROR severity counted
    projection = record.public_projection()
    assert projection["fileDigest"].startswith("sha256:")
    assert "/" not in str(projection["fileDigest"])  # digest, not a raw path


@pytest.mark.asyncio
async def test_enabled_with_clean_file_appends_nothing(tmp_path: Path) -> None:
    provider = _FakeProvider()
    bundle = build_gate5b_full_toolhost_bundle(
        config=_config(lsp_enabled=True),
        scope=_scope(),
        workspace_root=tmp_path,
        diagnostics_provider=provider,
    )
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "clean.py", "content": "value = 1\n"},
        request_digest=_sha256("req-2"),
        tool_call_id="call-2",
    )
    assert outcome.status == "ok"
    assert provider.calls  # provider ran
    preview = outcome.output_preview
    assert isinstance(preview, dict)
    assert "lspDiagnostics" not in preview
    assert outcome.code_diagnostics_receipt is None


@pytest.mark.asyncio
async def test_disabled_flag_is_fully_inert(tmp_path: Path) -> None:
    provider = _FakeProvider()
    bundle = build_gate5b_full_toolhost_bundle(
        config=_config(lsp_enabled=False),
        scope=_scope(),
        workspace_root=tmp_path,
        diagnostics_provider=provider,
    )
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "broken.py", "content": "raise_error = undefined_name\n"},
        request_digest=_sha256("req-3"),
        tool_call_id="call-3",
    )
    assert outcome.status == "ok"
    # Provider must never be consulted when the flag is OFF.
    assert provider.calls == []
    preview = outcome.output_preview
    assert isinstance(preview, dict)
    assert "lspDiagnostics" not in preview
    assert outcome.code_diagnostics_receipt is None


@pytest.mark.asyncio
async def test_enabled_skips_non_code_files(tmp_path: Path) -> None:
    provider = _FakeProvider()
    bundle = build_gate5b_full_toolhost_bundle(
        config=_config(lsp_enabled=True),
        scope=_scope(),
        workspace_root=tmp_path,
        diagnostics_provider=provider,
    )
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "notes.txt", "content": "raise_error here but not code\n"},
        request_digest=_sha256("req-4"),
        tool_call_id="call-4",
    )
    assert outcome.status == "ok"
    assert provider.calls == []  # .txt is not an LSP language
    assert outcome.code_diagnostics_receipt is None
