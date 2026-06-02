from __future__ import annotations

import json

from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
)

__all__ = ["MediaRemovalStrategy"]

_MEDIA_BLOCK_TYPES = frozenset({"image", "image_url", "document", "file"})
_PLACEHOLDER = "[Media removed due to size constraints]"


class MediaRemovalStrategy:
    """Remove image/document content blocks from messages."""

    def __init__(self, config: ErrorRecoveryConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "media_removal"

    def applies_to(self, error: RecoverableError) -> bool:
        return error.kind == ErrorKind.MEDIA_SIZE

    async def recover(
        self,
        context: RecoveryContext,
        state: RecoveryAttemptState | None = None,
    ) -> RecoveryResult:
        modified: list[MessageDict] = []
        tokens_freed = 0

        for msg in context.messages:
            content = msg.get("content")
            if not isinstance(content, list):
                modified.append(dict(msg))
                continue

            kept_blocks: list[dict[str, object]] = []
            removed_blocks: list[dict[str, object]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in _MEDIA_BLOCK_TYPES:
                    removed_blocks.append(block)
                else:
                    kept_blocks.append(block)

            if not removed_blocks:
                modified.append(dict(msg))
                continue

            for rb in removed_blocks:
                tokens_freed += len(json.dumps(rb, default=str)) // 4

            new_msg = dict(msg)
            if kept_blocks:
                new_msg["content"] = kept_blocks
            else:
                new_msg["content"] = _PLACEHOLDER
            modified.append(new_msg)

        return RecoveryResult(
            success=tokens_freed > 0,
            strategy_name=self.name,
            modified_messages=modified if tokens_freed > 0 else None,
            tokens_freed=tokens_freed,
        )
