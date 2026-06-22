"""E-15 — model-knob flags discoverable through the registry.

``cli/real_runner._model_retry_kwargs``, ``_model_reasoning_kwargs``, and
``_model_api_base_kwargs`` used to read 8 ``MAGI_*`` knobs inline via
``source.get(...)`` truthy/parse logic. That made them undiscoverable:
nothing enumerated "what knobs affect the model build" because they
were not in ``config/flags.py``'s ``FLAGS`` registry.

This test locks the new contract: every knob is a typed ``FlagSpec`` in
the registry, with the expected ``kind`` and default. ``MAGI_LLM_API_KEY``
is marked as a sensitive credential in its summary (matching the
existing pattern set by ``MAGI_DISCORD_BOT_TOKEN`` /
``MAGI_SLACK_BOT_TOKEN``).
"""

from __future__ import annotations

import pytest

from magi_agent.config.flags import FLAGS, FlagSpec, get_flag


_MODEL_KNOBS: tuple[tuple[str, str, object], ...] = (
    # name, kind, default
    ("MAGI_MODEL_NUM_RETRIES", "int", 4),
    ("MAGI_MODEL_TIMEOUT_S", "int", 600),
    ("MAGI_MODEL_THINKING_TYPE", "str", ""),
    ("MAGI_MODEL_THINKING_BUDGET_TOKENS", "int", 0),
    ("MAGI_MODEL_REASONING_EFFORT", "str", ""),
    ("MAGI_LLM_API_BASE", "str", ""),
    ("MAGI_LLM_API_KEY", "str", ""),
    ("MAGI_LLM_API_HEADER", "str", "x-api-key"),
)


@pytest.mark.parametrize("name,kind,default", _MODEL_KNOBS)
def test_model_knob_registered_with_expected_shape(
    name: str, kind: str, default: object
) -> None:
    spec = get_flag(name)
    assert isinstance(spec, FlagSpec)
    assert spec.kind == kind
    assert spec.default == default
    assert spec.scope == "public"


def test_every_model_knob_is_in_FLAGS_tuple() -> None:
    """Sanity guard: the registry tuple actually carries these entries (not
    just synthesized by a fallback)."""

    registered_names = {spec.name for spec in FLAGS}
    expected = {name for name, _kind, _default in _MODEL_KNOBS}
    missing = expected - registered_names
    assert missing == set(), f"Model knobs missing from FLAGS: {missing}"


def test_api_key_summary_calls_out_sensitivity() -> None:
    """``MAGI_LLM_API_KEY`` carries operator credentials. Match the
    existing pattern (``MAGI_DISCORD_BOT_TOKEN`` etc.) and note in the
    summary that it must not be logged/persisted — so a discovery dump
    surfaces the constraint to operators."""

    spec = get_flag("MAGI_LLM_API_KEY")
    text = spec.summary.lower()
    assert "logged" in text or "secret" in text or "credential" in text, (
        f"MAGI_LLM_API_KEY summary should call out its sensitivity: {spec.summary!r}"
    )


def test_unknown_flag_still_raises_through_registry() -> None:
    """Regression guard: registering 8 new flags must not loosen the
    "unknown name raises" contract."""

    with pytest.raises(LookupError):
        get_flag("MAGI_MODEL_NOT_A_REAL_KNOB")
