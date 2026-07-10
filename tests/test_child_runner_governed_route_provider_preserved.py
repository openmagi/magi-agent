"""PR-M (TRUE root fix): the governed branch must pass the provider-prefixed
LiteLLM model id into ``build_headless_runtime`` so the downstream
``resolve_provider_config`` does not mis-attribute the model to ``openai``.

Saga history:
* 0.1.62 -> 0.1.90, "SpawnAgent child returns silent empty" across anthropic
  + gemini routes. OpenAI-routed children worked because the misattribution
  defaulted to OpenAI.
* PR-L (#1130) added catalog-aware ``reasoning_effort`` derivation; necessary
  but not sufficient. The catalog lookup needed the right ``(provider, model)``
  pair, and the child runner was stripping the provider before handing the
  model string to ``build_headless_runtime``.

Direct-debug evidence (Kevin, 0.1.90):

    >>> from magi_agent.cli.providers import resolve_provider_config
    >>> resolve_provider_config(model_override="claude-opus-4-8")
    ProviderConfig(provider='openai', model='claude-opus-4-8', ...)
    >>> resolve_provider_config(model_override="anthropic/claude-opus-4-8")
    ProviderConfig(provider='anthropic', model='claude-opus-4-8', ...)

The bare model id falls into the openai branch of provider auto-detect; the
provider-prefixed slug is split first and the correct provider wins.

This file pins the contract: ``_collect_turn_text_governed`` MUST call
``build_headless_runtime(model=...)`` with the LiteLLM-style
``<provider>/<model>`` slug, falling back to the bare ``config.model`` only
when the config has no ``litellm_model`` property (preserves byte-identical
behaviour for mock-config test fixtures).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from magi_agent.cli.providers import ProviderConfig
from magi_agent.runtime.child_runner_boundary import ChildTaskRequest
from magi_agent.runtime.child_runner_live import (
    LIVE_CHILD_RUNNER_ENABLED_ENV,
    LIVE_CHILD_RUNNER_KILL_SWITCH_ENV,
    RealLocalChildRunner,
)

# Env vars that, if leaked from the operator shell (Kevin's dogfood-full-on
# profile sets ~96 MAGI_* knobs), would break test hermeticity. The fix has to
# work in any environment, so we strip all relevant ones up front.
_PROVIDER_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_ENABLED_ENV, raising=False)
    monkeypatch.delenv(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, raising=False)
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))


def _request(provider: str | None, model: str | None) -> ChildTaskRequest:
    return ChildTaskRequest(
        parentExecutionId="parent-exec-litellm",
        turnId="turn-litellm",
        taskId="task-litellm",
        objective="Drive ONE governed turn; assert the provider prefix survives.",
        role="general",
        delivery="return",
        provider=provider,
        model=model,
    )


def _install_governed_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    """Patch heavy ADK seams; return the list captured kwargs go into."""

    captured: list[dict[str, object]] = []

    def _fake_build_headless_runtime(**kwargs: object) -> object:
        captured.append(dict(kwargs))
        return object()

    async def _fake_governed_collector(
        _stream: object, **_kw: object
    ) -> tuple[str, tuple[str, ...], str, str | None]:
        # Non-empty summary so the silent-no-op guard does NOT fire; the route
        # capture is what we are asserting, not the silent-empty containment.
        return "ok", (), "completed", None

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_governed_collector,
    )
    return captured


class _EmptyStreamRunner:
    async def run_async(self, **kwargs: Any) -> AsyncGenerator[object, None]:
        return
        yield  # pragma: no cover - generator marker.


# --------------------------------------------------------------------------- #
# Contract: route_model preserves the LiteLLM provider prefix.                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_route_model_uses_litellm_model_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``config.litellm_model`` wins over bare ``config.model``.

    The ProviderConfig dataclass exposes both ``model`` (bare) and
    ``litellm_model`` (``<provider>/<model>``). The governed branch MUST
    pick the slug form so ``build_headless_runtime``'s downstream
    ``resolve_provider_config`` does not auto-detect the wrong provider.
    """
    captured = _install_governed_capture(monkeypatch)
    config = ProviderConfig(provider="anthropic", model="claude-opus-4-8", api_key="sk-test")

    child = RealLocalChildRunner(provider_config=config, runner=_EmptyStreamRunner())
    await child.run_child(_request(provider="anthropic", model="claude-opus-4-8"))

    assert len(captured) == 1, "build_headless_runtime should be called exactly once"
    assert captured[0].get("model") == "anthropic/claude-opus-4-8", (
        "Provider-prefixed slug MUST be forwarded; bare 'claude-opus-4-8' would "
        "mis-route via openai auto-detect in downstream resolve_provider_config."
    )


@pytest.mark.asyncio
async def test_route_model_falls_back_to_model_when_litellm_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mock config without ``litellm_model`` keeps the byte-identical
    pre-fix behaviour (test-fixture back-compat).
    """
    captured = _install_governed_capture(monkeypatch)

    class _MinimalConfig:
        # No ``litellm_model`` attribute on purpose.
        provider = "anthropic"
        model = "claude-opus-4-8"
        api_key = "sk-test"

    config = _MinimalConfig()
    child = RealLocalChildRunner(provider_config=config, runner=_EmptyStreamRunner())
    await child.run_child(_request(provider="anthropic", model="claude-opus-4-8"))

    assert len(captured) == 1
    assert captured[0].get("model") == "claude-opus-4-8", (
        "Without litellm_model the fallback uses bare config.model "
        "(byte-identical to pre-fix behaviour for legacy mock fixtures)."
    )


@pytest.mark.asyncio
async def test_anthropic_opus_routes_correctly_after_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repro shape from Kevin's 0.1.90 sandbox trace.

    Before this fix, ``resolve_provider_config(model_override='claude-opus-4-8')``
    returned ``provider='openai'`` (the auto-detect default). After this fix the
    slug ``'anthropic/claude-opus-4-8'`` flows in and ``_split_provider_slug``
    re-attributes the call to anthropic.
    """
    captured = _install_governed_capture(monkeypatch)
    config = ProviderConfig(provider="anthropic", model="claude-opus-4-8", api_key="sk-test")

    child = RealLocalChildRunner(provider_config=config, runner=_EmptyStreamRunner())
    await child.run_child(_request(provider="anthropic", model="claude-opus-4-8"))

    forwarded = captured[0].get("model")
    assert isinstance(forwarded, str)
    assert forwarded.startswith("anthropic/"), (
        f"Expected an anthropic-prefixed slug, got {forwarded!r}; downstream "
        "resolve_provider_config would auto-detect openai for bare ids."
    )


@pytest.mark.asyncio
async def test_gemini_route_preserves_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini was caught by the same misattribution.

    The ``gemini/`` prefix must survive into ``build_headless_runtime`` so the
    LiteLLM wrapper hits the Gemini provider (not openai).
    """
    captured = _install_governed_capture(monkeypatch)
    config = ProviderConfig(provider="gemini", model="gemini-3.1-pro-preview", api_key="x")

    child = RealLocalChildRunner(provider_config=config, runner=_EmptyStreamRunner())
    await child.run_child(_request(provider="gemini", model="gemini-3.1-pro-preview"))

    assert captured[0].get("model") == "gemini/gemini-3.1-pro-preview"


@pytest.mark.asyncio
async def test_openai_route_byte_identical_pre_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI happened to work pre-fix (auto-detect default was openai), but
    the slug shape is now uniform: every route ships ``<provider>/<model>``.
    """
    captured = _install_governed_capture(monkeypatch)
    config = ProviderConfig(provider="openai", model="gpt-5.5", api_key="sk-test")

    child = RealLocalChildRunner(provider_config=config, runner=_EmptyStreamRunner())
    await child.run_child(_request(provider="openai", model="gpt-5.5"))

    assert captured[0].get("model") == "openai/gpt-5.5"


@pytest.mark.asyncio
async def test_provider_aliased_to_litellm_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LiteLLM prefix on the captured slug MUST match the LiteLLM naming
    (driven by ``ProviderConfig.litellm_model`` -> ``_LITELLM_PREFIX``), not
    the registry alias.

    Concretely: a child route the registry knows as ``google/...`` is resolved
    into a ``ProviderConfig(provider='gemini', ...)`` (the CLI-side canonical
    name in ``SUPPORTED_PROVIDERS``), and the captured slug must begin with
    ``gemini/`` regardless of whether anything upstream said ``google``.
    """
    captured = _install_governed_capture(monkeypatch)
    config = ProviderConfig(provider="gemini", model="gemini-3.1-pro-preview", api_key="x")

    child = RealLocalChildRunner(provider_config=config, runner=_EmptyStreamRunner())
    await child.run_child(_request(provider="gemini", model="gemini-3.1-pro-preview"))

    forwarded = captured[0].get("model")
    assert isinstance(forwarded, str)
    prefix = forwarded.split("/", 1)[0]
    assert prefix == "gemini", (
        f"Expected the LiteLLM prefix 'gemini', got {prefix!r}; the registry "
        "alias 'google' must NOT leak into the LiteLLM-facing model slug."
    )
