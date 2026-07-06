"""Catalog invariant — every record with ``"reasoning"`` in capabilities must
emit a non-empty ``ModelCatalog.reasoning_default`` payload.

This forces future catalog edits to keep the E-6 default-ON contract honest:
adding a new reasoning-capable record without picking a ``reasoning_style`` ⇒
the model goes through the runtime with no reasoning kwargs even under the
flag, silently regressing benchmark numbers.

The corollary direction (records WITHOUT ``"reasoning"`` returning ``{}``) is
the existing default and does not need a separate assertion — but we lock in
the negative case for haiku / flash / cheap tiers as a smoke test so an
authoring slip there is caught too.
"""
from __future__ import annotations

import pytest

from magi_agent.models import ModelCatalog
from magi_agent.models.types import ModelRecord


def _records() -> list[ModelRecord]:
    return list(ModelCatalog.builtin().all_records())


# Records that are reasoning-capable but intentionally ship NO default
# reasoning kwargs: the provider reasons UNCONDITIONALLY with zero kwargs, so
# ``reasoning_style`` stays ``"none"`` on purpose. Fireworks Kimi is the case
# in point (verified live: it streams ``reasoning_content`` with no
# ``reasoning_effort``, and fireworks only accepts ``reasoning_effort`` for
# slugs litellm's cost map tags ``supports_reasoning``). Listing a row here is
# a CONSCIOUS authoring decision, which keeps the slip-guard below meaningful:
# a NEW reasoning record that forgets to pick an adaptive/effort style
# (defaulting to ``"none"``) only passes if the author also enrolls it here.
_REASONING_NO_KWARGS_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("fireworks", "kimi-k2p6"),
        ("fireworks", "kimi-k2p7"),
        ("fireworks", "kimi-k2p7-code"),
        ("fireworks", "kimi-k2p5"),
    }
)


def test_every_reasoning_capability_record_has_non_empty_default() -> None:
    catalog = ModelCatalog.builtin()
    offenders: list[str] = []
    for r in _records():
        if "reasoning" not in r.capabilities:
            continue
        if (r.provider, r.model) in _REASONING_NO_KWARGS_ALLOWLIST:
            continue
        payload = catalog.reasoning_default(r.provider, r.model)
        if not payload:
            offenders.append(f"{r.provider}/{r.model} ({r.reasoning_style!r})")
    assert not offenders, (
        "every catalog record with capabilities including 'reasoning' must "
        "produce a non-empty reasoning_default payload (set reasoning_style="
        "adaptive or effort), unless it is enrolled in the documented "
        "no-kwargs allowlist: " + ", ".join(offenders)
    )


def test_no_kwargs_allowlist_rows_are_reasoning_capable_style_none() -> None:
    """Every allowlisted row must actually exist, carry the ``"reasoning"``
    capability, use ``reasoning_style="none"``, and return ``{}`` so the
    allowlist can never silently hide a mis-styled adaptive/effort record."""
    catalog = ModelCatalog.builtin()
    for provider, model in sorted(_REASONING_NO_KWARGS_ALLOWLIST):
        record = catalog.record(provider, model)
        assert record is not None, f"allowlisted row missing: {provider}/{model}"
        assert "reasoning" in record.capabilities
        assert record.reasoning_style == "none"
        assert catalog.reasoning_default(provider, model) == {}


@pytest.mark.parametrize(
    "provider, model",
    [
        ("anthropic", "claude-haiku-4-5"),
        ("anthropic", "haiku"),
        ("openai", "gpt-5.4-mini"),
        ("openai", "gpt-5.4-nano"),
        ("gemini", "gemini-3.5-flash"),
        ("gemini", "gemini-3.1-flash-lite-preview"),
        ("fireworks", "minimax-m2p7"),
    ],
)
def test_non_reasoning_records_return_empty(provider: str, model: str) -> None:
    """Sanity: haiku / flash / cheap tiers ship no default reasoning kwargs.

    Kimi rows moved to ``test_no_kwargs_allowlist_rows_are_reasoning_capable_
    style_none``: they are now reasoning-capable (still ``{}`` default) so they
    no longer belong in the non-reasoning sanity set."""
    catalog = ModelCatalog.builtin()
    assert catalog.reasoning_default(provider, model) == {}


def test_unknown_provider_or_model_returns_empty() -> None:
    catalog = ModelCatalog.builtin()
    assert catalog.reasoning_default("anthropic", "no-such-model") == {}
    assert catalog.reasoning_default("not-a-provider", "anything") == {}


def test_adaptive_and_effort_shapes_are_distinct() -> None:
    """Pin the two payload shapes E-6 ships so a future refactor that collapses
    them (e.g. relying on litellm to map adaptive ↔ effort) trips here first."""
    catalog = ModelCatalog.builtin()
    assert catalog.reasoning_default("anthropic", "claude-opus-4-8") == {
        "thinking": {"type": "adaptive"}
    }
    assert catalog.reasoning_default("anthropic", "claude-sonnet-4-6") == {
        "reasoning_effort": "high"
    }
