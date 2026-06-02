from __future__ import annotations


def test_context_attachments_include_channel_memory_and_safe_file_label() -> None:
    from openmagi_core_agent.runtime.context_attachments import (
        build_current_turn_context_attachments,
    )

    attachments = build_current_turn_context_attachments(
        channel={"type": "app", "channelId": "default", "memoryMode": "normal"},
        user_message={
            "attachments": [
                {
                    "kind": "file",
                    "name": "report.pdf",
                    "mimeType": "application/pdf",
                    "localPath": "/workspace/bot/telegram-downloads/report.pdf",
                }
            ]
        },
        workspace_root="/workspace/bot",
    )

    rendered = repr([item.model_dump(by_alias=True) for item in attachments])
    assert "channel" in rendered
    assert "memory_mode" in rendered
    assert "telegram-downloads/report.pdf" in rendered
    assert "/workspace/bot" not in rendered


def test_context_attachments_reject_private_payload_markers() -> None:
    from openmagi_core_agent.runtime.context_attachments import (
        build_current_turn_context_attachments,
    )

    attachments = build_current_turn_context_attachments(
        channel={"type": "telegram", "channelId": "123", "memoryMode": "read_only"},
        user_message={
            "metadata": {
                "rawToolLogs": ["REDACT_ME_AUTH_SENTINEL"],
                "privatePath": "/workspace/private/secret.txt",
                "systemPromptAddendum": "Safe KB note.",
            }
        },
        workspace_root="/workspace/bot",
    )

    rendered = repr([item.model_dump(by_alias=True) for item in attachments])
    assert "Safe KB note." in rendered
    assert "REDACT_ME_AUTH_SENTINEL" not in rendered
    assert "/workspace/private" not in rendered
