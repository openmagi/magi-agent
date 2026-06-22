"""When the parent passes ``model="provider:model"`` or ``"provider/model"``
to SpawnAgent, the child runner's ``_resolve_route`` must extract the
provider rather than concat it as a model id.

Pre-fix, ``model="anthropic:claude-sonnet-4-6"`` flowed verbatim to the
registry lookup, which then asked for ``provider="anthropic"`` +
``model="anthropic:claude-sonnet-4-6"`` — never resolves. Kevin's repro
saw the route surface as ``anthropic:anthropic:claude-sonnet-4-6``
(double prefix) because the litellm route string assembled the second
``anthropic:`` from the model itself.

Same shape for ``"provider/model"`` (LiteLLM's wire form). The fix
splits both forms in ``_resolve_route`` BEFORE the registry lookup so
the parent can use either convention and the child sees a canonical
``(provider, model)`` pair.

This is the SAME normalisation downstream code does for the chat-route
``modelOverride`` field — bringing the spawn path to parity.
"""
from __future__ import annotations

from magi_agent.runtime.child_runner_live import RealLocalChildRunner


class _Request:
    def __init__(self, *, provider: object = None, model: object = None) -> None:
        self.provider = provider
        self.model = model


def _resolve(request: _Request) -> tuple[str, str]:
    runner = RealLocalChildRunner()
    return runner._resolve_route(request)


def test_colon_form_in_model_splits_provider_out() -> None:
    p, m = _resolve(_Request(model="anthropic:claude-sonnet-4-6"))
    assert p == "anthropic"
    assert m == "claude-sonnet-4-6"


def test_slash_form_in_model_splits_provider_out() -> None:
    # LiteLLM's wire form — parents may copy it verbatim. Treat the same.
    p, m = _resolve(_Request(model="openai/gpt-5.5"))
    assert p == "openai"
    assert m == "gpt-5.5"


def test_explicit_provider_takes_precedence_over_model_prefix() -> None:
    # If both are supplied, the explicit ``provider`` field wins (user
    # intent) and the model is taken AS A WHOLE — the split only runs when
    # the parent has packed the route into ``model`` alone.
    p, m = _resolve(
        _Request(provider="anthropic", model="claude-sonnet-4-6")
    )
    assert p == "anthropic"
    assert m == "claude-sonnet-4-6"


def test_explicit_provider_with_colon_in_model_does_not_double_split() -> None:
    # Tricky: parent passes both. Even if ``model`` accidentally has a
    # provider prefix, the explicit field still wins — but the prefix in
    # ``model`` is stripped so we never assemble the double-prefix route.
    p, m = _resolve(
        _Request(provider="anthropic", model="anthropic:claude-sonnet-4-6")
    )
    assert p == "anthropic"
    assert m == "claude-sonnet-4-6"


def test_plain_model_without_provider_prefix_unchanged() -> None:
    # Back-compat: a bare model id with no prefix must NOT be mangled.
    p, m = _resolve(_Request(model="claude-sonnet-4-6"))
    # Default provider applied (no explicit provider, no prefix found).
    assert p  # whatever the default fallback is
    assert m == "claude-sonnet-4-6"


def test_empty_provider_segment_in_model_is_ignored() -> None:
    # Defensive: ``model=":claude-sonnet-4-6"`` (empty provider segment)
    # must not produce ``provider=""``. Fall back to the default.
    p, m = _resolve(_Request(model=":claude-sonnet-4-6"))
    assert p  # default applied — never the empty string
    assert m == "claude-sonnet-4-6"


def test_empty_model_segment_after_prefix_falls_back_to_default_model() -> None:
    # ``model="anthropic:"`` — provider extracted, model segment empty.
    # Fall back to the default child model instead of using "" as model.
    p, m = _resolve(_Request(model="anthropic:"))
    assert p == "anthropic"
    assert m  # default applied — never empty


def test_provider_only_path_unchanged() -> None:
    # When ``model`` is not supplied at all, the historical default-route
    # behavior is byte-identical (this is the spawn-with-provider-only
    # path that has been working since #791).
    p, m = _resolve(_Request(provider="openai", model=None))
    assert p == "openai"
    assert m  # provider's default model from the config layer
