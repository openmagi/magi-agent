from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.runtime.content_replacement import ContentReplacement
from magi_agent.runtime.content_replacement import replace_content_with_ref


def test_content_replacement_projects_digest_ref_preview_only_for_large_content() -> None:
    replacement = replace_content_with_ref(
        content_kind="tool_result",
        raw_content="alpha beta gamma " * 200,
        ref_namespace="tool-result",
        preview_chars=32,
    )

    projection = replacement.public_projection()
    assert projection["contentRef"].startswith("tool-result:")
    assert projection["digest"].startswith("sha256:")
    assert projection["preview"].startswith("[content preview sha256:")
    assert projection["originalBytes"] > 32
    assert projection["truncated"] is True
    assert "rawContent" not in projection


def test_content_replacement_redacts_unsafe_prompt_output_logs_paths_and_secrets() -> None:
    replacement = replace_content_with_ref(
        content_kind="tool_log",
        raw_content=(
            "raw prompt: inspect /Users/kevin/private/file.txt\n"
            "Authorization: Bearer live-token\n"
            "stdout: sk-live-secret"
        ),
        ref_namespace="tool-log",
    )

    encoded = json.dumps(replacement.public_projection(), sort_keys=True)
    assert replacement.preview == "[redacted unsafe content]"
    assert replacement.redacted is True
    assert "/Users/kevin" not in encoded
    assert "Authorization" not in encoded
    assert "live-token" not in encoded
    assert "sk-live-secret" not in encoded


def test_content_replacement_direct_construction_rejects_raw_preview_and_bad_digest() -> None:
    with pytest.raises(ValidationError, match="sha256"):
        ContentReplacement(
            contentKind="tool-log",
            contentRef="tool-log:abc123",
            digest="not-a-digest",
            preview="[content preview sha256:1234567890abcdef bytes:10]",
            originalBytes=10,
            truncated=False,
            redacted=False,
        )

    with pytest.raises(ValidationError, match="preview"):
        ContentReplacement(
            contentKind="tool-log",
            contentRef="tool-log:abc123",
            digest="sha256:" + "a" * 64,
            preview="raw user prompt /Users/kevin/private Authorization: Bearer token",
            originalBytes=10,
            truncated=False,
            redacted=False,
        )


def test_content_replacement_model_copy_revalidates_preview_and_digest() -> None:
    replacement = replace_content_with_ref(
        content_kind="tool_result",
        raw_content="safe local fake result",
        ref_namespace="tool-result",
    )

    with pytest.raises(ValidationError, match="preview"):
        replacement.model_copy(update={"preview": "raw user prompt"})
    with pytest.raises(ValidationError, match="sha256"):
        replacement.model_copy(update={"digest": "not-a-digest"})
