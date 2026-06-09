"""Convert sanitized Anthropic-style image blocks into ADK content parts.

Pure module: the ADK Part constructor is injected via ``part_factory`` so this
has no google-genai dependency and is unit-testable in isolation. Input blocks
are assumed already sanitized upstream (supported media type, valid base64,
byte caps); malformed entries are skipped defensively.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = ["image_blocks_to_parts"]

PartFactory = Callable[..., Any]


def image_blocks_to_parts(
    blocks: Sequence[object],
    *,
    part_factory: PartFactory,
) -> list[Any]:
    parts: list[Any] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != "image":
            continue
        source = block.get("source")
        if not isinstance(source, Mapping) or source.get("type") != "base64":
            continue
        media_type = source.get("media_type")
        data = source.get("data")
        if not isinstance(media_type, str) or not isinstance(data, str):
            continue
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not raw:
            continue
        parts.append(part_factory(data=raw, mime_type=media_type))
    return parts
