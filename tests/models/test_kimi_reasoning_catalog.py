"""PR-1 (kimi reasoning catalog honesty).

Kimi is a reasoning model: it streams ``reasoning_content`` unconditionally
(verified live against fireworks). The catalog must say so via the
``"reasoning"`` capability, WITHOUT starting to send reasoning kwargs.
``reasoning_style`` is the request-kwargs axis, so it stays ``"none"``: Kimi
reasons with zero kwargs and fireworks only accepts ``reasoning_effort`` for
slugs litellm's cost map tags ``supports_reasoning``.

This file also locks in the missing ``fireworks/kimi-k2p7`` record.
"""
from __future__ import annotations

import pytest

from magi_agent.models.catalog import ModelCatalog

# The Kimi rows that are reasoning-capable but ship NO default reasoning
# kwargs (style "none"). Mirrors the allowlist in
# ``test_reasoning_default_meta._REASONING_NO_KWARGS_ALLOWLIST``.
_KIMI_REASONING_ROWS = [
    ("fireworks", "kimi-k2p6"),
    ("fireworks", "kimi-k2p7"),
    ("fireworks", "kimi-k2p7-code"),
    ("fireworks", "kimi-k2p5"),
]


@pytest.mark.parametrize("provider, model", _KIMI_REASONING_ROWS)
def test_kimi_rows_are_reasoning_capable(provider: str, model: str) -> None:
    record = ModelCatalog.builtin().record(provider, model)
    assert record is not None, f"catalog missing {provider}/{model}"
    assert "reasoning" in record.capabilities, (
        f"{provider}/{model} must carry the 'reasoning' capability (Kimi "
        "streams reasoning_content unconditionally)"
    )


@pytest.mark.parametrize("provider, model", _KIMI_REASONING_ROWS)
def test_kimi_rows_keep_reasoning_style_none(provider: str, model: str) -> None:
    """Style is the request-kwargs axis; Kimi needs no kwargs, so it stays
    ``"none"`` and ``reasoning_default`` must remain empty (no overbilling /
    no 400 from fireworks on a slug that does not accept reasoning_effort)."""
    catalog = ModelCatalog.builtin()
    record = catalog.record(provider, model)
    assert record is not None
    assert record.reasoning_style == "none"
    assert catalog.reasoning_default(provider, model) == {}


def test_kimi_k2p7_record_added() -> None:
    """The plain (non-code) ``kimi-k2p7`` slug now has a catalog record,
    mirroring ``kimi-k2p7-code`` window/output parameters."""
    catalog = ModelCatalog.builtin()
    record = catalog.record("fireworks", "kimi-k2p7")
    assert record is not None, "catalog missing fireworks/kimi-k2p7"
    code = catalog.record("fireworks", "kimi-k2p7-code")
    assert code is not None
    assert record.context_window == code.context_window
    assert record.max_output_tokens == code.max_output_tokens
    assert record.litellm_prefix == "fireworks_ai"
    assert record.tier == "cheap"
    assert record.deprecated is False
    assert "reasoning" in record.capabilities
