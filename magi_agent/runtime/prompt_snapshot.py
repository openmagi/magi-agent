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
