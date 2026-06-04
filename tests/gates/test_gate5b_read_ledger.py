from __future__ import annotations

import hashlib

import pytest

from magi_agent.config.env import is_read_ledger_enabled
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


def _ready_bundle(tmp_path, *, read_ledger_enabled: bool):
    return build_gate5b_full_toolhost_bundle(
        config=_config(),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        read_ledger_enabled=read_ledger_enabled,
    )


# ---- flag plumbing ----------------------------------------------------------


def test_flag_is_default_off() -> None:
    assert is_read_ledger_enabled({}) is False
    assert is_read_ledger_enabled({"MAGI_READ_LEDGER_ENABLED": "0"}) is False
    assert is_read_ledger_enabled({"MAGI_READ_LEDGER_ENABLED": ""}) is False


def test_flag_parses_truthy_values() -> None:
    for value in ("1", "true", "TRUE", "yes", "on"):
        assert is_read_ledger_enabled({"MAGI_READ_LEDGER_ENABLED": value}) is True


def test_host_ledger_enabled_only_when_flag_on(tmp_path) -> None:
    on = _ready_bundle(tmp_path, read_ledger_enabled=True)
    off = _ready_bundle(tmp_path, read_ledger_enabled=False)
    assert on.host.read_ledger.config.enabled is True
    assert on.host.read_ledger.config.local_in_memory_enabled is True
    assert off.host.read_ledger.config.enabled is False


# ---- (a) edit a never-read file is blocked no_prior_read --------------------


@pytest.mark.asyncio
async def test_edit_without_prior_read_blocked(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "alpha", "newText": "beta"},
        request_digest=_sha256("req-edit-noread"),
        tool_call_id="call-edit-noread",
    )

    assert outcome.status == "blocked"
    assert outcome.reason == "read_ledger_no_prior_read"
    # file unchanged on disk
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "alpha\n"


# ---- (a2) blocked-by-ledger mutation is RETRYABLE after a read --------------


@pytest.mark.asyncio
async def test_ledger_blocked_edit_is_retryable_after_read(tmp_path) -> None:
    """The core recovery flow: blocked no_prior_read must not poison dedup.

    Model attempts FileEdit (blocked no_prior_read) -> reads the file ->
    retries the IDENTICAL FileEdit (same request_digest + tool_call_id +
    args). The retry must SUCCEED, not be rejected as duplicate_tool_call.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    edit_args = {"path": "src/app.py", "oldText": "alpha", "newText": "beta"}
    edit_request = _sha256("req-edit-retry")
    edit_call_id = "call-edit-retry"

    blocked = await bundle.host.dispatch(
        "FileEdit",
        edit_args,
        request_digest=edit_request,
        tool_call_id=edit_call_id,
    )
    assert blocked.status == "blocked"
    assert blocked.reason == "read_ledger_no_prior_read"

    read = await bundle.host.dispatch(
        "FileRead",
        {"path": "src/app.py"},
        request_digest=_sha256("req-read-retry"),
        tool_call_id="call-read-retry",
    )
    assert read.status == "ok"

    # Identical retry of the SAME edit must now go through.
    retry = await bundle.host.dispatch(
        "FileEdit",
        edit_args,
        request_digest=edit_request,
        tool_call_id=edit_call_id,
    )
    assert retry.status == "ok", retry.reason
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "beta\n"


@pytest.mark.asyncio
async def test_non_ledger_block_still_dedupes(tmp_path) -> None:
    """Genuine duplicate protection for non-ledger blocks must remain intact.

    A ``tool_not_allowlisted`` block records its receipt; an identical retry
    is NOT silently re-run (the block is permanent, not retryable). It must
    stay ``blocked`` rather than turning into ``ok``.
    """

    bundle = build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerDigest": _sha256("user-test"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                # Only Clock allowlisted -> FileEdit is not allowlisted.
                "allowedToolNames": ("Clock",),
                "maxToolCallsPerTurn": 16,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
        },
        workspace_root=tmp_path,
        read_ledger_enabled=True,
    )

    first = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "a", "newText": "b"},
        request_digest=_sha256("req-not-allow"),
        tool_call_id="call-not-allow",
    )
    assert first.status == "blocked"
    assert first.reason == "tool_not_allowlisted"

    second = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "a", "newText": "b"},
        request_digest=_sha256("req-not-allow"),
        tool_call_id="call-not-allow",
    )
    # Non-ledger block was recorded; identical retry is NOT re-run as ok.
    assert second.status == "duplicate"
    assert second.reason == "duplicate_tool_call"
    assert second.handler_called is False


# ---- (b) read then edit is allowed -----------------------------------------


@pytest.mark.asyncio
async def test_read_then_edit_allowed(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    read = await bundle.host.dispatch(
        "FileRead",
        {"path": "src/app.py"},
        request_digest=_sha256("req-read"),
        tool_call_id="call-read",
    )
    assert read.status == "ok"

    edit = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "alpha", "newText": "beta"},
        request_digest=_sha256("req-edit"),
        tool_call_id="call-edit",
    )
    assert edit.status == "ok"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "beta\n"


# ---- (c) read, then file changes on disk, then edit is blocked stale --------


@pytest.mark.asyncio
async def test_read_then_stale_edit_blocked(tmp_path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    (target / "app.py").write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    read = await bundle.host.dispatch(
        "FileRead",
        {"path": "src/app.py"},
        request_digest=_sha256("req-read"),
        tool_call_id="call-read",
    )
    assert read.status == "ok"

    # external change on disk after read
    (target / "app.py").write_text("alpha\nextra\n", encoding="utf-8")

    edit = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "alpha", "newText": "beta"},
        request_digest=_sha256("req-edit-stale"),
        tool_call_id="call-edit-stale",
    )
    assert edit.status == "blocked"
    assert edit.reason == "read_ledger_stale_read_digest"
    assert (target / "app.py").read_text(encoding="utf-8") == "alpha\nextra\n"


# ---- (d) create a brand-new file is allowed (no prior read needed) ----------


@pytest.mark.asyncio
async def test_create_new_file_allowed(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "src/brand_new.py", "content": "print('new')\n"},
        request_digest=_sha256("req-create"),
        tool_call_id="call-create",
    )
    assert outcome.status == "ok"
    assert (tmp_path / "src" / "brand_new.py").read_text(encoding="utf-8") == (
        "print('new')\n"
    )


@pytest.mark.asyncio
async def test_overwrite_existing_file_requires_prior_read(tmp_path) -> None:
    (tmp_path / "data.txt").write_text("old\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    blocked = await bundle.host.dispatch(
        "FileWrite",
        {"path": "data.txt", "content": "new\n"},
        request_digest=_sha256("req-overwrite-noread"),
        tool_call_id="call-overwrite-noread",
    )
    assert blocked.status == "blocked"
    assert blocked.reason == "read_ledger_no_prior_read"
    assert (tmp_path / "data.txt").read_text(encoding="utf-8") == "old\n"

    await bundle.host.dispatch(
        "FileRead",
        {"path": "data.txt"},
        request_digest=_sha256("req-read-data"),
        tool_call_id="call-read-data",
    )
    ok = await bundle.host.dispatch(
        "FileWrite",
        {"path": "data.txt", "content": "new\n"},
        request_digest=_sha256("req-overwrite-ok"),
        tool_call_id="call-overwrite-ok",
    )
    assert ok.status == "ok"
    assert (tmp_path / "data.txt").read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_patch_apply_overwrite_requires_prior_read(tmp_path) -> None:
    (tmp_path / "doc.md").write_text("# title\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    blocked = await bundle.host.dispatch(
        "PatchApply",
        {"path": "doc.md", "content": "# new\n"},
        request_digest=_sha256("req-patch-noread"),
        tool_call_id="call-patch-noread",
    )
    assert blocked.status == "blocked"
    assert blocked.reason == "read_ledger_no_prior_read"
    assert (tmp_path / "doc.md").read_text(encoding="utf-8") == "# title\n"


@pytest.mark.asyncio
async def test_patch_apply_create_new_file_allowed(tmp_path) -> None:
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"path": "fresh.md", "content": "# fresh\n"},
        request_digest=_sha256("req-patch-create"),
        tool_call_id="call-patch-create",
    )
    assert outcome.status == "ok"
    assert (tmp_path / "fresh.md").read_text(encoding="utf-8") == "# fresh\n"


# ---- partial read -> full_read_required ------------------------------------


@pytest.mark.asyncio
async def test_partial_read_blocks_full_read_required(tmp_path) -> None:
    """A recorded PARTIAL read must surface ``read_ledger_full_read_required``.

    The gate5b FileRead path always records ``read_mode="full"``, so this
    reason cannot arise from the normal dispatch flow. We exercise the
    enforce->ledger->reason translation directly by recording a partial read
    against the host's ledger using its real workspace ref, then editing.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)
    host = bundle.host

    digest = "sha256:" + hashlib.sha256("alpha\n".encode("utf-8")).hexdigest()
    recorded = host.read_ledger.record_read(
        session_id="gate5b-read-ledger",
        workspace_ref=host._read_ledger_workspace_ref,
        path="src/app.py",
        digest=digest,
        size_bytes=6,
        mtime_ns=0,
        read_mode="partial",
        turn_id="gate5b-turn",
        tool_use_id="gate5b-file-read",
    )
    assert recorded is not None
    assert recorded.read_mode == "partial"

    edit = await host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "alpha", "newText": "beta"},
        request_digest=_sha256("req-edit-partial"),
        tool_call_id="call-edit-partial",
    )
    assert edit.status == "blocked"
    assert edit.reason == "read_ledger_full_read_required"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "alpha\n"


# ---- TOCTOU: file disappears between is_file() and read --------------------


@pytest.mark.asyncio
async def test_file_disappears_during_enforce_is_clean_block(tmp_path, monkeypatch) -> None:
    """If the file vanishes between is_file() and read_text(), surface a clear
    block (not a generic tool_error)."""

    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    real_read_text = type(target).read_text

    def exploding_read_text(self, *args, **kwargs):
        if self.name == "app.py":
            raise FileNotFoundError("vanished")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "read_text", exploding_read_text)

    edit = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "alpha", "newText": "beta"},
        request_digest=_sha256("req-edit-toctou"),
        tool_call_id="call-edit-toctou",
    )
    assert edit.status == "blocked"
    assert edit.reason == "read_ledger_file_disappeared_during_read"


# ---- (e) flag OFF: no checks (existing behavior) ---------------------------


@pytest.mark.asyncio
async def test_flag_off_edit_without_read_is_unchecked(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("alpha\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=False)

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "src/app.py", "oldText": "alpha", "newText": "beta"},
        request_digest=_sha256("req-edit-off"),
        tool_call_id="call-edit-off",
    )
    assert outcome.status == "ok"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "beta\n"


@pytest.mark.asyncio
async def test_flag_off_overwrite_without_read_is_unchecked(tmp_path) -> None:
    (tmp_path / "data.txt").write_text("old\n", encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=False)

    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "data.txt", "content": "new\n"},
        request_digest=_sha256("req-overwrite-off"),
        tool_call_id="call-overwrite-off",
    )
    assert outcome.status == "ok"
    assert (tmp_path / "data.txt").read_text(encoding="utf-8") == "new\n"


# ---- large file: full digest recorded even when read output truncated -------


@pytest.mark.asyncio
async def test_large_file_full_read_records_full_digest(tmp_path) -> None:
    # content larger than the configured max_per_tool_output_bytes preview cap.
    big = "x" * (8192 * 2) + "\n"
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    bundle = _ready_bundle(tmp_path, read_ledger_enabled=True)

    read = await bundle.host.dispatch(
        "FileRead",
        {"path": "big.txt"},
        request_digest=_sha256("req-read-big"),
        tool_call_id="call-read-big",
    )
    assert read.status == "ok"

    edit = await bundle.host.dispatch(
        "FileEdit",
        {"path": "big.txt", "oldText": "x", "newText": "y"},
        request_digest=_sha256("req-edit-big"),
        tool_call_id="call-edit-big",
    )
    assert edit.status == "ok"
