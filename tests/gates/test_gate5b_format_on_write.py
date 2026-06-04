"""PR4: Gate 5B format-after-edit integration via real host.dispatch.

Determinism: the integration test never depends on ``ruff``/``prettier`` being
installed. It injects a tiny deterministic "formatter" script through
``MAGI_FORMATTER_OVERRIDES`` (``.py=<python> <script> $FILE``). The script
rewrites the written file to a known formatted state, so assertions are stable
regardless of which real formatters happen to be on the test runner.
"""
from __future__ import annotations

import hashlib
import sys

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    Gate5BFullToolHostConfig,
    _digest,
    build_gate5b_full_toolhost_bundle,
)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _config(*, format_on_write: bool) -> Gate5BFullToolHostConfig:
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
            "formatOnWriteEnabled": format_on_write,
        }
    )


def _scope() -> dict[str, str]:
    return {
        "selectedBotDigest": _sha256("bot-test"),
        "selectedOwnerDigest": _sha256("user-test"),
        "environment": "production",
    }


def _bundle(tmp_path, *, format_on_write: bool):
    return build_gate5b_full_toolhost_bundle(
        config=_config(format_on_write=format_on_write),
        scope=_scope(),
        workspace_root=tmp_path,
    )


def _install_fake_formatter(tmp_path, monkeypatch, *, formatted_text: str) -> None:
    """Point .py formatting at a deterministic script that rewrites the file."""
    script = tmp_path / "fakefmt.py"
    script.write_text(
        "import sys\n"
        "p = sys.argv[1]\n"
        f"open(p, 'w', encoding='utf-8').write({formatted_text!r})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "MAGI_FORMATTER_OVERRIDES", f".py={sys.executable} {script} $FILE"
    )


@pytest.mark.asyncio
async def test_format_on_write_formats_disk_and_digest_matches_formatted(
    tmp_path, monkeypatch
):
    formatted = "x = 1\n"
    _install_fake_formatter(tmp_path, monkeypatch, formatted_text=formatted)
    bundle = _bundle(tmp_path, format_on_write=True)

    misformatted = "x=1\n"
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "pkg/module.py", "content": misformatted},
        request_digest=_sha256("req-fmt-1"),
        tool_call_id="call-fmt-write",
    )

    assert outcome.status == "ok"
    written = (tmp_path / "pkg/module.py").read_text(encoding="utf-8")
    # File on disk is the formatted state.
    assert written == formatted
    # Returned pathDigest reflects the formatted content (re-read), so the
    # model's next FileEdit can match the formatted text.
    assert outcome.output_preview["pathDigest"] == _digest(formatted)


@pytest.mark.asyncio
async def test_format_on_write_applies_to_file_edit(tmp_path, monkeypatch):
    formatted = "y = 2\n"
    _install_fake_formatter(tmp_path, monkeypatch, formatted_text=formatted)
    bundle = _bundle(tmp_path, format_on_write=True)
    target = tmp_path / "edit.py"
    target.write_text("y=9\n", encoding="utf-8")

    outcome = await bundle.host.dispatch(
        "FileEdit",
        {"path": "edit.py", "oldText": "y=9", "newText": "y=2"},
        request_digest=_sha256("req-fmt-2"),
        tool_call_id="call-fmt-edit",
    )

    assert outcome.status == "ok"
    assert target.read_text(encoding="utf-8") == formatted
    assert outcome.output_preview["pathDigest"] == _digest(formatted)


@pytest.mark.asyncio
async def test_format_on_write_applies_to_patch_apply(tmp_path, monkeypatch):
    formatted = "z = 3\n"
    _install_fake_formatter(tmp_path, monkeypatch, formatted_text=formatted)
    bundle = _bundle(tmp_path, format_on_write=True)

    outcome = await bundle.host.dispatch(
        "PatchApply",
        {"path": "patch.py", "content": "z=3\n"},
        request_digest=_sha256("req-fmt-3"),
        tool_call_id="call-fmt-patch",
    )

    assert outcome.status == "ok"
    assert (tmp_path / "patch.py").read_text(encoding="utf-8") == formatted


@pytest.mark.asyncio
async def test_missing_formatter_write_succeeds_unformatted(tmp_path, monkeypatch):
    # No override; force the selected formatter to look uninstalled so the test
    # is independent of whether ruff is actually present on the runner.
    monkeypatch.delenv("MAGI_FORMATTER_OVERRIDES", raising=False)
    monkeypatch.setenv("MAGI_FORMATTER_OVERRIDES", ".py=definitely-not-installed $FILE")
    bundle = _bundle(tmp_path, format_on_write=True)

    content = "a=1\n"
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "nofmt.py", "content": content},
        request_digest=_sha256("req-fmt-4"),
        tool_call_id="call-fmt-missing",
    )

    assert outcome.status == "ok"
    # Write still succeeded, unformatted (fail-open).
    assert (tmp_path / "nofmt.py").read_text(encoding="utf-8") == content
    # Digest still reflects the (unformatted) on-disk content.
    assert outcome.output_preview["pathDigest"] == _digest(content)


@pytest.mark.asyncio
async def test_flag_off_does_not_format_and_keeps_path_digest(tmp_path, monkeypatch):
    # Even with a working fake formatter available, OFF means no formatting and
    # the legacy path-string digest (zero regression).
    _install_fake_formatter(tmp_path, monkeypatch, formatted_text="SHOULD_NOT_APPEAR\n")
    bundle = _bundle(tmp_path, format_on_write=False)

    content = "b=2\n"
    outcome = await bundle.host.dispatch(
        "FileWrite",
        {"path": "pkg/off.py", "content": content},
        request_digest=_sha256("req-fmt-5"),
        tool_call_id="call-fmt-off",
    )

    assert outcome.status == "ok"
    # Not formatted.
    assert (tmp_path / "pkg/off.py").read_text(encoding="utf-8") == content
    # Legacy path-string digest preserved (zero regression).
    assert outcome.output_preview["pathDigest"] == _digest("pkg/off.py")


def test_flag_default_off_via_env_threading():
    from magi_agent.config.env import is_format_on_write_enabled

    assert is_format_on_write_enabled({}) is False
    assert is_format_on_write_enabled({"MAGI_EDIT_FORMAT_ON_WRITE_ENABLED": "1"}) is True
    assert (
        is_format_on_write_enabled({"MAGI_EDIT_FORMAT_ON_WRITE_ENABLED": "false"})
        is False
    )
