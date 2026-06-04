from __future__ import annotations

import hashlib

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bundle(tmp_path, *, apply_patch_enabled=False, model_id="", allowed=None):
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": allowed or GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 16,
            "applyPatchEnabled": apply_patch_enabled,
            "applyPatchModelId": model_id,
        }
    )
    return build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_envelope_multi_file_patch_applies(tmp_path):
    (tmp_path / "old.py").write_text("gone\n", encoding="utf-8")
    (tmp_path / "edit.py").write_text("x\nkeep\n", encoding="utf-8")
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+created\n"
        "*** Delete File: old.py\n"
        "*** Update File: edit.py\n"
        "@@\n"
        "-x\n"
        "+y\n"
        " keep\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-1"),
        tool_call_id="call-patch-1",
    )
    assert outcome.status == "ok"
    assert outcome.output_preview["patchMode"] == "envelope"
    assert outcome.output_preview["fileCount"] == 3
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "created\n"
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "edit.py").read_text(encoding="utf-8") == "y\nkeep\n"


@pytest.mark.asyncio
async def test_envelope_patch_atomic_failure_writes_nothing(tmp_path):
    (tmp_path / "edit.py").write_text("x\n", encoding="utf-8")
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+created\n"
        "*** Update File: edit.py\n"
        "@@\n"
        "-NOT_PRESENT\n"
        "+y\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-2"),
        tool_call_id="call-patch-2",
    )
    # Verification failure surfaces as a tool error, and NOTHING is written.
    assert outcome.status == "error"
    assert not (tmp_path / "new.py").exists()
    assert (tmp_path / "edit.py").read_text(encoding="utf-8") == "x\n"


@pytest.mark.asyncio
async def test_envelope_patch_path_escape_rejected(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: ../escape.py\n"
        "+nope\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-3"),
        tool_call_id="call-patch-3",
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "patch_path_traversal"
    assert not (tmp_path.parent / "escape.py").exists()


@pytest.mark.asyncio
async def test_envelope_patch_sealed_path_rejected(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    # ".env" hits the sensitive-path policy (dotfile + env keyword).
    patch = (
        "*** Begin Patch\n"
        "*** Add File: .env\n"
        "+SECRET=1\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-4"),
        tool_call_id="call-patch-4",
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "secret_path_denied"
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
async def test_content_replace_still_works_when_enabled(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"path": "whole.txt", "content": "full body\n"},
        request_digest=_sha256("req-5"),
        tool_call_id="call-patch-5",
    )
    assert outcome.status == "ok"
    assert outcome.output_preview["patchMode"] == "content_replace"
    assert (tmp_path / "whole.txt").read_text(encoding="utf-8") == "full body\n"


@pytest.mark.asyncio
async def test_flag_off_preserves_unsupported_patch_shape(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=False)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: new.py\n"
        "+created\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-6"),
        tool_call_id="call-patch-6",
    )
    assert outcome.status == "error"
    assert not (tmp_path / "new.py").exists()


def test_gpt5_routing_exposes_apply_patch_excludes_edit_write(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True, model_id="openai:gpt-5.5")
    exposed = set(bundle.exposed_tool_names)
    assert "PatchApply" in exposed
    assert "FileWrite" not in exposed
    assert "FileEdit" not in exposed
    # Read/search tools survive.
    assert {"FileRead", "Glob", "Grep"} <= exposed


def test_non_gpt5_keeps_edit_write(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True, model_id="anthropic:haiku")
    exposed = set(bundle.exposed_tool_names)
    assert "FileWrite" in exposed
    assert "FileEdit" in exposed
    assert "PatchApply" in exposed


def test_gpt4_is_not_gpt5_class(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True, model_id="openai:gpt-4o")
    exposed = set(bundle.exposed_tool_names)
    assert "FileWrite" in exposed
    assert "FileEdit" in exposed


def test_flag_off_keeps_edit_write_for_gpt5(tmp_path):
    # With the flag OFF, no swap happens even for GPT-5.
    bundle = _bundle(tmp_path, apply_patch_enabled=False, model_id="openai:gpt-5.5")
    exposed = set(bundle.exposed_tool_names)
    assert "FileWrite" in exposed
    assert "FileEdit" in exposed


def test_gpt50_is_not_gpt5_class(tmp_path):
    # "gpt-50" must NOT match the gpt-5 family (trailing digit boundary). If it
    # were mis-classified, FileWrite/FileEdit would be swapped out.
    bundle = _bundle(tmp_path, apply_patch_enabled=True, model_id="openai:gpt-50")
    exposed = set(bundle.exposed_tool_names)
    assert "FileWrite" in exposed
    assert "FileEdit" in exposed


@pytest.mark.asyncio
async def test_update_missing_surfaces_update_target_missing(tmp_path):
    # Updating a MISSING (but path-safe) file must surface plan_patch's precise
    # reason via the gate, not be masked as path_policy_denied by the preflight.
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    patch = (
        "*** Begin Patch\n"
        "*** Update File: missing.py\n"
        "@@\n"
        "-x\n"
        "+y\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-update-missing"),
        tool_call_id="call-update-missing",
    )
    # plan_patch existence failure -> patch_apply_error (ValueError path),
    # surfaced as an "error" outcome, NOT a "blocked" path_policy_denied.
    assert outcome.status == "error"
    assert not (tmp_path / "missing.py").exists()


@pytest.mark.asyncio
async def test_delete_missing_surfaces_delete_target_missing(tmp_path):
    bundle = _bundle(tmp_path, apply_patch_enabled=True)
    patch = (
        "*** Begin Patch\n"
        "*** Delete File: missing.py\n"
        "*** End Patch\n"
    )
    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"patch": patch},
        request_digest=_sha256("req-delete-missing"),
        tool_call_id="call-delete-missing",
    )
    assert outcome.status == "error"
