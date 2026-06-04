from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenPromptSnapshot(BaseModel):
    """Captures the exact system prompt bytes at a point in time for fork cache sharing."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    blocks: tuple[dict[str, Any], ...] = Field(alias="blocks")
    fingerprint: str = Field(alias="fingerprint")

    @classmethod
    def capture(cls, system_prompt_blocks: list[dict[str, Any]]) -> FrozenPromptSnapshot:
        """Deep-copy and freeze system prompt blocks. SHA256 fingerprint for cache key validation."""
        frozen = tuple(copy.deepcopy(block) for block in system_prompt_blocks)
        canonical = json.dumps(frozen, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(blocks=frozen, fingerprint=fingerprint)

    def restore(self) -> list[dict[str, Any]]:
        """Returns the exact same blocks (byte-identical via deep copy)."""
        return [copy.deepcopy(dict(block)) for block in self.blocks]


def _strip_cache_control(value: Any) -> Any:
    """Recursively drop ``cache_control`` markers so they cannot affect a hash.

    Cache markers are rolling/ephemeral hints (the message-tail injector adds
    them to the last ~2 conversation messages each turn). They carry no
    semantic content, so excluding them keeps a fingerprint stable across turns
    where only the marker placement changed.
    """
    if isinstance(value, dict):
        return {
            key: _strip_cache_control(item)
            for key, item in value.items()
            if key != "cache_control"
        }
    if isinstance(value, (list, tuple)):
        return [_strip_cache_control(item) for item in value]
    return value


def message_tail_fingerprint(messages: list[dict[str, Any]]) -> str:
    """SHA256 fingerprint of conversation messages, ignoring cache markers.

    Used to validate fork cache sharing against the conversation tail without
    being destabilised by the rolling ``cache_control`` markers that the
    message-tail injector adds. Two message lists that differ only in cache
    marker placement produce the same fingerprint (rule 4); any change to the
    underlying text/roles changes it.
    """
    normalized = [_strip_cache_control(message) for message in messages]
    canonical = json.dumps(
        normalized, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
