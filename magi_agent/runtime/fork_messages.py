from __future__ import annotations

import copy
from typing import Any


FORK_PLACEHOLDER_RESULT = "[fork-placeholder: tool result omitted for cache sharing]"


def build_forked_messages(
    *,
    parent_assistant_message: dict[str, Any],
    directive: str,
) -> list[dict[str, Any]]:
    """Build forked child messages with shared prefix and per-child directive.

    The parent's assistant message (with tool_use blocks) is preserved.
    All tool_results use FORK_PLACEHOLDER_RESULT (identical across children).
    Only the final directive text block differs per child.
    """
    assistant = copy.deepcopy(parent_assistant_message)
    if assistant.get("role") != "assistant":
        raise ValueError("parent_assistant_message must have role 'assistant'")

    content = assistant.get("content")
    if not isinstance(content, list):
        raise ValueError("parent_assistant_message must have list content")

    tool_use_ids = [
        block["id"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]

    tool_results_message: dict[str, Any] = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": FORK_PLACEHOLDER_RESULT,
            }
            for tool_id in tool_use_ids
        ],
    }

    directive_message: dict[str, Any] = {
        "role": "user",
        "content": directive,
    }

    return [assistant, tool_results_message, directive_message]
