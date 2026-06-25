"""Hosted-path pack loading (per-tenant dir), default-OFF, signing gate applied.

The hosted serving toolhost is assembled by
``magi_agent.gates.gate5b_full_toolhost.build_gate5b_full_toolhost_bundle`` whose
pack-loaded workspace runtime comes from the process-memoized
``_pack_loaded_workspace_runtime`` (which walks ``default_search_bases`` ->
bundled first-party + ~/.magi + cwd). The hosted path must NOT pull in ~/.magi or
cwd: hosted packs live ONLY under ``MAGI_HOSTED_PACKS_DIR`` and load only when
``MAGI_HOSTED_PACKS_ENABLED`` is ON. The COMMIT-1 signing gate applies to them.

These tests exercise the seam function ``build_hosted_pack_workspace_runtime``
directly (a full transport request is too heavy for the hermetic env) plus the
bundle merge so the activation is observable on the live host object.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.gates.gate5b_full_toolhost import (
    build_gate5b_full_toolhost_bundle,
    build_hosted_pack_workspace_runtime,
)
from magi_agent.packs.signing import compute_pack_digest
from magi_agent.packs.discovery import discover_pack_files

_HOSTED_TOOL_NAME = "HostedEchoTool"

_PACK_TOML = """\
packId = "user.hosted-echo-pack"
displayName = "Hosted Echo Pack"
version = "0.1.0"
description = "Hosted per-tenant tool pack for the hosted-loading test."

[[provides]]
type = "tool"
ref = "HostedEchoTool"
impl = "hosted_echo_pack.impl:provide"
"""

_IMPL_PY = '''\
"""Hosted tool provider that binds a workspace handler."""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.packs.context import ToolProvideContext, WorkspaceHostView
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _handle(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    return {"echo": str(args.get("text", ""))}


def provide(context: ToolProvideContext) -> None:
    manifest = ToolManifest(
        name="HostedEchoTool",
        description="Echo the provided text back to the caller.",
        kind="external",
        source=ToolSource(kind="external", package="user.hosted-echo-pack"),
        permission="read",
        input_schema=_INPUT_SCHEMA,
        timeout_ms=30_000,
        budget=Budget(max_calls_per_turn=10, max_parallel=1),
        dangerous=False,
        is_concurrency_safe=True,
        mutates_workspace=False,
        parallel_safety="readonly",
        available_in_modes=("plan", "act"),
        tags=("user",),
        enabled_by_default=True,
        opt_out=True,
    )
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("HostedEchoTool", _handle)
'''


def _write_hosted_pack(packs_dir: Path) -> None:
    pack_dir = packs_dir / "hosted_echo_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(_PACK_TOML)
    (pack_dir / "impl.py").write_text(_IMPL_PY)


def test_off_loads_no_hosted_packs(tmp_path: Path, monkeypatch) -> None:
    hosted = tmp_path / "tenant-packs"
    _write_hosted_pack(hosted)
    monkeypatch.delenv("MAGI_HOSTED_PACKS_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_HOSTED_PACKS_DIR", str(hosted))

    handlers, policies = build_hosted_pack_workspace_runtime()
    assert handlers == {}
    assert policies == ()


def test_on_without_dir_loads_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_HOSTED_PACKS_ENABLED", "1")
    monkeypatch.delenv("MAGI_HOSTED_PACKS_DIR", raising=False)

    handlers, policies = build_hosted_pack_workspace_runtime()
    assert handlers == {}
    assert policies == ()


def test_on_with_trusted_pack_activates_primitive(tmp_path: Path, monkeypatch) -> None:
    hosted = tmp_path / "tenant-packs"
    _write_hosted_pack(hosted)
    digest = compute_pack_digest(discover_pack_files([hosted])[0])
    monkeypatch.setenv("MAGI_HOSTED_PACKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_HOSTED_PACKS_DIR", str(hosted))
    # Signing required + the hosted pack's digest is in the allowlist.
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", digest)

    handlers, _policies = build_hosted_pack_workspace_runtime()
    assert _HOSTED_TOOL_NAME in handlers


def test_on_untrusted_pack_dropped_when_signing_required(
    tmp_path: Path, monkeypatch
) -> None:
    hosted = tmp_path / "tenant-packs"
    _write_hosted_pack(hosted)
    monkeypatch.setenv("MAGI_HOSTED_PACKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_HOSTED_PACKS_DIR", str(hosted))
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", "deadbeef")

    handlers, _policies = build_hosted_pack_workspace_runtime()
    assert _HOSTED_TOOL_NAME not in handlers


def test_bundle_merges_hosted_handler_onto_host(tmp_path: Path, monkeypatch) -> None:
    hosted = tmp_path / "tenant-packs"
    _write_hosted_pack(hosted)
    digest = compute_pack_digest(discover_pack_files([hosted])[0])
    monkeypatch.setenv("MAGI_HOSTED_PACKS_ENABLED", "1")
    monkeypatch.setenv("MAGI_HOSTED_PACKS_DIR", str(hosted))
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", digest)

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    bundle = build_gate5b_full_toolhost_bundle(workspace_root=workspace_root)
    # The hosted pack's workspace handler is merged onto the live host.
    assert _HOSTED_TOOL_NAME in bundle.host._workspace_handlers  # noqa: SLF001


def test_bundle_off_does_not_load_hosted_packs(tmp_path: Path, monkeypatch) -> None:
    hosted = tmp_path / "tenant-packs"
    _write_hosted_pack(hosted)
    monkeypatch.delenv("MAGI_HOSTED_PACKS_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_HOSTED_PACKS_DIR", str(hosted))

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    bundle = build_gate5b_full_toolhost_bundle(workspace_root=workspace_root)
    assert _HOSTED_TOOL_NAME not in bundle.host._workspace_handlers  # noqa: SLF001
