from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import magi_agent.gates.gate5b_full_toolhost as gate5b_module
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
    # Model-facing block must use the relative path so the model knows which
    # file to fix — NOT an opaque sha256 digest.
    assert 'file="broken.py"' in block, (
        f"Expected relative path label in diagnostics block, got: {block!r}"
    )
    assert "ERROR [1:1] boom" in block
    # WARNING severity must be filtered out.
    assert "just a warning" not in block
    # Digest must NOT appear as the file label in the model-visible block.
    assert "sha256:" not in block

    record = outcome.code_diagnostics_receipt
    assert record is not None
    assert record.type == "CodeDiagnostics"
    assert record.error_count == 1  # only ERROR severity counted
    projection = record.public_projection()
    # Evidence record still uses digest (public-safety), never the raw path.
    assert projection["fileDigest"].startswith("sha256:")
    assert "broken.py" not in str(projection["fileDigest"])  # digest, not path


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
async def test_host_shutdown_tears_down_owned_lsp_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the host lazily owns a real LspClient (no injected provider),
    host.shutdown() must call shutdown_all() so per-request servers are reaped.
    """

    class _FakeLspClient:
        instances: list[_FakeLspClient] = []

        def __init__(self, workspace_root: Path, *, timeout_s: float) -> None:
            self.shutdown_calls = 0
            self.diagnostics_calls = 0
            _FakeLspClient.instances.append(self)

        def diagnostics(self, path: Path, text: str) -> list[Diagnostic]:
            self.diagnostics_calls += 1
            return [
                Diagnostic(line=1, column=1, severity=SEVERITY_ERROR, message="boom")
            ]

        def shutdown_all(self) -> None:
            self.shutdown_calls += 1

    monkeypatch.setattr(gate5b_module, "LspClient", _FakeLspClient)

    bundle = build_gate5b_full_toolhost_bundle(
        config=_config(lsp_enabled=True),
        scope=_scope(),
        workspace_root=tmp_path,
        diagnostics_provider=None,  # force the host to build a real LspClient
    )
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "broken.py", "content": "x = y\n"},
        request_digest=_sha256("req-shutdown"),
        tool_call_id="call-shutdown",
    )
    assert outcome.status == "ok"
    assert len(_FakeLspClient.instances) == 1
    owned = _FakeLspClient.instances[0]
    assert owned.diagnostics_calls == 1
    assert owned.shutdown_calls == 0

    bundle.host.shutdown()
    assert owned.shutdown_calls == 1


@pytest.mark.asyncio
async def test_host_shutdown_does_not_touch_injected_provider(
    tmp_path: Path,
) -> None:
    """An injected (test) provider is NOT owned by the host, so shutdown() must
    not attempt to tear it down (it has no shutdown_all)."""
    provider = _FakeProvider()
    bundle = build_gate5b_full_toolhost_bundle(
        config=_config(lsp_enabled=True),
        scope=_scope(),
        workspace_root=tmp_path,
        diagnostics_provider=provider,
    )
    await bundle.host.dispatch(
        "FileWrite",
        {"path": "broken.py", "content": "raise_error = undefined_name\n"},
        request_digest=_sha256("req-inj"),
        tool_call_id="call-inj",
    )
    # Must not raise even though _FakeProvider has no shutdown_all.
    bundle.host.shutdown()


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
