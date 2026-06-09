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


def _config() -> Gate5BFullToolHostConfig:
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
            "maxToolCallsPerTurn": 16,
        }
    )


def _ready_bundle(tmp_path, *, memory_mode: str = "normal"):
    return build_gate5b_full_toolhost_bundle(
        config=_config(),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        read_ledger_enabled=False,
        memory_mode=memory_mode,
    )


# ---- threading -------------------------------------------------------------


def test_memory_mode_threaded_into_host(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, memory_mode="incognito")
    assert bundle.host.memory_mode == "incognito"


def test_memory_mode_defaults_normal(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path)
    assert bundle.host.memory_mode == "normal"


# ---- write blocking --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["read_only", "incognito"])
async def test_filewrite_protected_memory_blocked(tmp_path, mode) -> None:
    bundle = _ready_bundle(tmp_path, memory_mode=mode)

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "MEMORY.md", "content": "new memory"},
        request_digest=_sha256(f"req-write-{mode}"),
        tool_call_id=f"call-write-{mode}",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"
    assert not (tmp_path / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_filewrite_protected_memory_blocked_via_filepath_alias(tmp_path) -> None:
    # gate5b FileWrite/FileEdit accept the ``filePath`` alias; the write guard
    # must cover it, not just ``path``.
    bundle = _ready_bundle(tmp_path, memory_mode="read_only")

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"filePath": "MEMORY.md", "content": "new memory"},
        request_digest=_sha256("req-write-filepath"),
        tool_call_id="call-write-filepath",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"
    assert not (tmp_path / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_filewrite_protected_memory_not_blocked_in_normal(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, memory_mode="normal")

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "MEMORY.md", "content": "new memory"},
        request_digest=_sha256("req-write-normal"),
        tool_call_id="call-write-normal",
    )

    # Normal mode must not block for the memory-mode reason. It may succeed or
    # fail for unrelated sandbox reasons, but never memory_mode_blocked.
    assert outcome.reason != "memory_mode_blocked"


@pytest.mark.asyncio
async def test_filewrite_non_protected_path_not_blocked(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, memory_mode="incognito")

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "src/app.py", "content": "print('ok')\n"},
        request_digest=_sha256("req-write-src"),
        tool_call_id="call-write-src",
    )

    assert outcome.reason != "memory_mode_blocked"


@pytest.mark.asyncio
async def test_bash_redirect_into_memory_blocked_read_only(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, memory_mode="read_only")

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "echo x >> MEMORY.md"},
        request_digest=_sha256("req-bash-write"),
        tool_call_id="call-bash-write",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"
    assert not (tmp_path / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_bash_mention_memory_blocked_incognito(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, memory_mode="incognito")

    outcome = await bundle.host.dispatch(
        "Bash",
        {"command": "cat MEMORY.md"},
        request_digest=_sha256("req-bash-read"),
        tool_call_id="call-bash-read",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"


# ---- read blocking ---------------------------------------------------------


@pytest.mark.asyncio
async def test_fileread_protected_memory_blocked_incognito(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("secret memory\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, memory_mode="incognito")

    outcome = await bundle.host.dispatch(
        "FileRead",
        {"path": "MEMORY.md"},
        request_digest=_sha256("req-read-incognito"),
        tool_call_id="call-read-incognito",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"


@pytest.mark.asyncio
async def test_fileread_protected_memory_blocked_read_only(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("secret memory\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, memory_mode="read_only")

    outcome = await bundle.host.dispatch(
        "FileRead",
        {"path": "MEMORY.md"},
        request_digest=_sha256("req-read-read-only"),
        tool_call_id="call-read-read-only",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"


@pytest.mark.asyncio
async def test_fileread_protected_memory_not_blocked_normal(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("memory\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, memory_mode="normal")

    outcome = await bundle.host.dispatch(
        "FileRead",
        {"path": "MEMORY.md"},
        request_digest=_sha256("req-read-normal"),
        tool_call_id="call-read-normal",
    )

    assert outcome.reason != "memory_mode_blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["read_only", "incognito"])
async def test_broad_grep_protected_memory_blocked(tmp_path, mode) -> None:
    (tmp_path / "MEMORY.md").write_text("needle protected\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("needle public\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, memory_mode=mode)

    outcome = await bundle.host.dispatch(
        "Grep",
        {"pattern": "needle"},
        request_digest=_sha256(f"req-grep-{mode}"),
        tool_call_id=f"call-grep-{mode}",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "memory_mode_blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["read_only", "incognito"])
async def test_glob_protected_memory_filtered(tmp_path, mode) -> None:
    (tmp_path / "MEMORY.md").write_text("protected top\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "daily.md").write_text("protected dir\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("public\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, memory_mode=mode)

    outcome = await bundle.host.dispatch(
        "Glob",
        {"pattern": "**/*"},
        request_digest=_sha256(f"req-glob-{mode}"),
        tool_call_id=f"call-glob-{mode}",
    )

    assert outcome.status == "ok"
    assert outcome.reason != "memory_mode_blocked"
    matches = set(outcome.output_preview["matches"])
    assert "src/note.txt" in matches
    assert "MEMORY.md" not in matches
    assert "memory/daily.md" not in matches
