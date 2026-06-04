import hashlib

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bundle(tmp_path, *, read_quality_enabled, read_max_lines=2000, max_bytes=8192):
    return build_gate5b_full_toolhost_bundle(
        config=Gate5BFullToolHostConfig.model_validate(
            {
                "enabled": True,
                "killSwitchEnabled": False,
                "routeAttachmentEnabled": True,
                "selectedBotDigest": _sha256("bot"),
                "selectedOwnerDigest": _sha256("user"),
                "environment": "production",
                "environmentAllowlist": ("production",),
                "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
                "maxToolCallsPerTurn": 16,
                "maxPerToolOutputBytes": max_bytes,
                "readQualityEnabled": read_quality_enabled,
                "readMaxLines": read_max_lines,
            }
        ),
        scope={
            "selectedBotDigest": _sha256("bot"),
            "selectedOwnerDigest": _sha256("user"),
            "environment": "production",
        },
        workspace_root=tmp_path,
    )


async def _read(bundle, path, *, args=None, call="c"):
    extra = dict(args or {})
    return await bundle.host.dispatch(
        "FileRead",
        {"path": path, **extra},
        request_digest=_sha256("req"),
        tool_call_id=call,
    )


@pytest.mark.asyncio
async def test_flag_on_adds_line_numbers(tmp_path):
    (tmp_path / "code.py").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=True)
    outcome = await _read(bundle, "code.py")
    assert outcome.status == "ok"
    content = outcome.output_preview["content"]
    assert content.startswith("1: alpha")
    assert "2: beta" in content
    assert "3: gamma" in content
    assert "lineNumberGuidance" in outcome.output_preview


@pytest.mark.asyncio
async def test_flag_off_preserves_original_behavior(tmp_path):
    (tmp_path / "code.py").write_text("alpha\nbeta\n", encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=False)
    outcome = await _read(bundle, "code.py")
    assert outcome.status == "ok"
    content = outcome.output_preview["content"]
    assert content == "alpha\nbeta\n"
    assert "1: alpha" not in content
    assert "lineNumberGuidance" not in outcome.output_preview


@pytest.mark.asyncio
async def test_large_file_capped_with_offset_footer_and_paging(tmp_path):
    lines = "\n".join(f"row{i}" for i in range(1, 21)) + "\n"
    (tmp_path / "big.txt").write_text(lines, encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=True, read_max_lines=5)
    outcome = await _read(bundle, "big.txt")
    assert outcome.output_preview["truncated"] is True
    content = outcome.output_preview["content"]
    assert "1: row1" in content
    assert "use offset=6 to continue" in content
    assert outcome.output_preview["nextOffset"] == 6

    # Page using the returned offset.
    paged = await _read(bundle, "big.txt", args={"offset": 6}, call="c2")
    paged_content = paged.output_preview["content"]
    assert "6: row6" in paged_content
    assert "1: row1" not in paged_content


@pytest.mark.asyncio
async def test_binary_file_returns_cannot_read_message(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\x03binary\x00data")
    bundle = _bundle(tmp_path, read_quality_enabled=True)
    outcome = await _read(bundle, "blob.bin")
    assert outcome.status == "ok"
    assert outcome.output_preview["binary"] is True
    assert outcome.output_preview["message"] == "Cannot read binary file"


@pytest.mark.asyncio
async def test_missing_file_suggests_did_you_mean(tmp_path):
    (tmp_path / "readme.txt").write_text("hi\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("k: v\n", encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=True)
    outcome = await _read(bundle, "redme.txt")
    assert outcome.output_preview.get("fileNotFound") is True
    assert "readme.txt" in outcome.output_preview["suggestions"]
    assert "Did you mean?" in outcome.output_preview["message"]


@pytest.mark.asyncio
async def test_redaction_applied_before_numbering(tmp_path):
    (tmp_path / "creds.txt").write_text(
        "line one\ntoken=sk-supersecretkey1234567890\nline three\n", encoding="utf-8"
    )
    bundle = _bundle(tmp_path, read_quality_enabled=True)
    outcome = await _read(bundle, "creds.txt")
    content = outcome.output_preview["content"]
    assert "sk-supersecretkey1234567890" not in content
    assert "[redacted]" in content
    # Numbering still present and correct after redaction.
    assert content.startswith("1: line one")
    assert "2: " in content


@pytest.mark.asyncio
async def test_missing_file_does_not_leak_secret_names(tmp_path):
    # A sensitive sibling must never appear as a suggestion.
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=True)
    outcome = await _read(bundle, "note.txt")
    suggestions = outcome.output_preview.get("suggestions", [])
    assert ".env" not in suggestions


@pytest.mark.asyncio
async def test_workspace_escape_path_no_suggestions_and_no_outside_listing(tmp_path):
    """Escape paths (../../etc/passwd) must NOT produce suggestions and must NOT
    list any directory outside the workspace — not even via did-you-mean.

    The gate blocks the request (status='blocked', output_preview=None) — it
    must not reach the did-you-mean branch at all for escape paths.
    """
    # Create a file in the workspace whose name resembles the escape target.
    (tmp_path / "passwd").write_text("workspace-only\n", encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=True)

    # A classic directory-traversal attempt.
    outcome = await _read(bundle, "../../etc/passwd")
    # The gate must block with no output_preview — no suggestions surfaced.
    assert outcome.status == "blocked", (
        f"Expected blocked, got {outcome.status!r}"
    )
    assert outcome.output_preview is None, (
        f"Escape path leaked output_preview: {outcome.output_preview}"
    )


@pytest.mark.asyncio
async def test_within_workspace_dotdot_path_no_outside_listing(tmp_path):
    """A path like 'subdir/../../etc/passwd' (uses '..' segments) must be
    blocked with no did-you-mean suggestions leaking outside the workspace."""
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "notes.txt").write_text("hi\n", encoding="utf-8")
    bundle = _bundle(tmp_path, read_quality_enabled=True)

    outcome = await _read(bundle, "subdir/../../etc/passwd")
    # Must be blocked — no output_preview, no suggestions.
    assert outcome.status == "blocked", (
        f"Expected blocked, got {outcome.status!r}"
    )
    assert outcome.output_preview is None, (
        f"Path with '..' leaked output_preview: {outcome.output_preview}"
    )
