"""Tests for production critic-model resolution in the egress gate (PR3).

The egress critic gate is wired but, before this change, ``_egress_critic_model_factory``
only honoured a test-only payload key and returned ``None`` in production — so a
flag-ON gate produced no grounding signal in prod. These tests prove the
production path now resolves a real Haiku-class model via the SAME mechanism the
SmartApprove read-only classifier uses (``resolve_provider_config`` ->
``_build_litellm_for_config``), while keeping:

  (a) the test-injection override taking precedence (hermetic tests),
  (b) fail-open when no provider config / key is resolvable, and
  (c) NO real LLM / network calls (provider resolution + builder are stubbed).
"""

from __future__ import annotations

from dataclasses import dataclass

import magi_agent.transport.chat as chat


# ---------------------------------------------------------------------------
# Stubs shaped like the real ProviderConfig + LiteLlm
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeProviderConfig:
    """Shaped like magi_agent.cli.providers.ProviderConfig."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = "sk-test-not-real"

    @property
    def litellm_model(self) -> str:
        return f"anthropic/{self.model}"


class _FakeModel:
    """Stands in for an ADK LiteLlm instance (no network)."""

    def __init__(self, model: str) -> None:
        self.model = model


# ---------------------------------------------------------------------------
# (b) Fail-open: no provider config -> None
# ---------------------------------------------------------------------------


def test_production_factory_none_when_no_provider_config(monkeypatch) -> None:
    """resolve_provider_config() -> None => factory is None (gate dormant)."""
    import magi_agent.cli.providers as providers

    monkeypatch.setattr(providers, "resolve_provider_config", lambda *a, **k: None)

    factory = chat._egress_critic_model_factory({"messages": []})
    assert factory is None


def test_production_factory_none_when_resolution_raises(monkeypatch) -> None:
    """A raising resolver must fail open (None), never propagate."""
    import magi_agent.cli.providers as providers

    def _boom(*_a, **_k):
        raise RuntimeError("provider blew up")

    monkeypatch.setattr(providers, "resolve_provider_config", _boom)

    factory = chat._egress_critic_model_factory({"messages": []})
    assert factory is None


# ---------------------------------------------------------------------------
# (a) Production resolves a real (stubbed) model when config is present
# ---------------------------------------------------------------------------


def test_production_factory_builds_model_from_provider_config(monkeypatch) -> None:
    """A resolvable provider config => factory returns a non-None model.

    Both the provider resolver and the LiteLlm builder are stubbed so no real
    model is constructed and no network call happens — we only assert the prod
    path threads the resolved config into the same builder SmartApprove uses.
    """
    import magi_agent.cli.providers as providers
    import magi_agent.cli.readonly_classifier as rc

    cfg = _FakeProviderConfig()
    monkeypatch.setattr(providers, "resolve_provider_config", lambda *a, **k: cfg)

    captured: dict[str, object] = {}

    def _fake_build(provider_config, *, model_override=None):
        captured["provider_config"] = provider_config
        captured["model_override"] = model_override
        return _FakeModel(model=provider_config.litellm_model)

    monkeypatch.setattr(rc, "_build_litellm_for_config", _fake_build)
    monkeypatch.delenv("MAGI_EGRESS_CRITIC_MODEL", raising=False)

    factory = chat._egress_critic_model_factory({"messages": []})
    assert factory is not None

    model = factory()
    assert isinstance(model, _FakeModel)
    assert model.model == "anthropic/claude-sonnet-4-6"
    # The SAME provider config object is threaded into the SmartApprove builder.
    assert captured["provider_config"] is cfg
    # No fast-model override env set -> None passed through.
    assert captured["model_override"] is None


def test_production_factory_honours_haiku_override_env(monkeypatch) -> None:
    """MAGI_EGRESS_CRITIC_MODEL is forwarded to the builder as model_override."""
    import magi_agent.cli.providers as providers
    import magi_agent.cli.readonly_classifier as rc

    cfg = _FakeProviderConfig()
    monkeypatch.setattr(providers, "resolve_provider_config", lambda *a, **k: cfg)

    captured: dict[str, object] = {}

    def _fake_build(provider_config, *, model_override=None):
        captured["model_override"] = model_override
        return _FakeModel(model=model_override or provider_config.litellm_model)

    monkeypatch.setattr(rc, "_build_litellm_for_config", _fake_build)
    monkeypatch.setenv("MAGI_EGRESS_CRITIC_MODEL", "anthropic/claude-haiku-4-6")

    factory = chat._egress_critic_model_factory({"messages": []})
    assert factory is not None
    model = factory()
    assert isinstance(model, _FakeModel)
    assert captured["model_override"] == "anthropic/claude-haiku-4-6"
    assert model.model == "anthropic/claude-haiku-4-6"


def test_factory_raise_in_build_is_safe_caller_fails_open(monkeypatch) -> None:
    """If the builder raises at call time, the consumer must fail open.

    The factory itself returns a callable; the caller (run_egress_critic_check /
    FactCriticalClassifier) wraps ``factory()`` in try/except -> None. Here we
    assert the factory is callable AND that invoking it raising does NOT escape
    when consumed the way the gate consumes it.
    """
    import magi_agent.cli.providers as providers
    import magi_agent.cli.readonly_classifier as rc

    cfg = _FakeProviderConfig()
    monkeypatch.setattr(providers, "resolve_provider_config", lambda *a, **k: cfg)

    def _raising_build(provider_config, *, model_override=None):
        raise RuntimeError("litellm dependency not available")

    monkeypatch.setattr(rc, "_build_litellm_for_config", _raising_build)

    factory = chat._egress_critic_model_factory({"messages": []})
    assert factory is not None

    # FactCriticalClassifier._resolve_model wraps factory() and returns None.
    from magi_agent.introspection.fact_critical import FactCriticalClassifier

    classifier = FactCriticalClassifier(model_factory=factory)
    assert classifier._resolve_model() is None


# ---------------------------------------------------------------------------
# (c) Test-injection override takes precedence
# ---------------------------------------------------------------------------


def test_test_injection_override_wins_over_production(monkeypatch) -> None:
    """The payload _egressCriticModelFactory key beats the production resolver."""
    import magi_agent.cli.providers as providers

    # Make production resolution "succeed" so we can prove the override wins.
    monkeypatch.setattr(
        providers,
        "resolve_provider_config",
        lambda *a, **k: _FakeProviderConfig(),
    )

    sentinel_model = _FakeModel(model="injected")

    def _injected_factory() -> object:
        return sentinel_model

    payload = {
        "messages": [],
        "_egressCriticModelFactory": _injected_factory,
    }
    factory = chat._egress_critic_model_factory(payload)
    assert factory is _injected_factory
    assert factory() is sentinel_model


def test_non_mapping_payload_uses_production_path(monkeypatch) -> None:
    """A non-Mapping payload has no override key -> production path runs."""
    import magi_agent.cli.providers as providers

    monkeypatch.setattr(providers, "resolve_provider_config", lambda *a, **k: None)
    assert chat._egress_critic_model_factory(object()) is None
