"""E-7 — single seam for the cache-aware Anthropic decision.

The CLI/headless/TUI path (``cli/real_runner._maybe_build_cache_aware_anthropic``)
and the hosted serve path
(``shadow/gate5b4c3_live_runner_boundary._gate1a_correlated_model_or_label``)
each contained an independent copy of the "is this an Anthropic route?
→ build_cache_aware_claude(...)" decision. The two could drift (e.g.
one updating to a new cache marker version, the other staying behind;
or one adding a guard the other lacks).

This module consolidates the decision in
``magi_agent/runtime/model_factory.py`` and locks the seam with a
meta-test forbidding any new caller of ``build_cache_aware_claude``
outside the factory + the definition.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Stub config — the factory only reads ``provider``, ``model``, ``api_key``;
# accepts ProviderConfig in practice but a duck-typed stub keeps the test
# from depending on cli/providers internals.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StubConfig:
    provider: str
    model: str = "claude-sonnet-4-6"
    api_key: str = "sk-test"


_SENTINEL = object()


@pytest.fixture(autouse=True)
def _reset_anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _patch_build(monkeypatch: pytest.MonkeyPatch, *, raises: Exception | None = None) -> dict[str, Any]:
    from magi_agent.runtime import model_factory

    calls: dict[str, Any] = {}

    def _build(model: str) -> object:
        calls["model"] = model
        if raises is not None:
            raise raises
        return _SENTINEL

    monkeypatch.setattr(model_factory, "build_cache_aware_claude", _build, raising=False)
    return calls


# ---------------------------------------------------------------------------
# Happy path: anthropic + cache ON + no custom endpoint → cache-aware
# ---------------------------------------------------------------------------


def test_anthropic_cache_on_returns_cache_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.runtime import model_factory

    calls = _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    out = model_factory.maybe_build_cache_aware_anthropic(_StubConfig("anthropic"), env={})
    assert out is _SENTINEL
    assert calls["model"] == "claude-sonnet-4-6"  # bare id, not provider-prefixed


def test_non_anthropic_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    assert (
        model_factory.maybe_build_cache_aware_anthropic(_StubConfig("openai"), env={})
        is None
    )


# ---------------------------------------------------------------------------
# Flag gating
# ---------------------------------------------------------------------------


def test_cache_flag_off_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: False
    )
    assert (
        model_factory.maybe_build_cache_aware_anthropic(
            _StubConfig("anthropic"), env={}
        )
        is None
    )


def test_gate_on_flag_false_bypasses_flag_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hosted serve path (``shadow/gate5b4c3``) calls this with
    ``gate_on_flag=False`` to preserve its existing unconditional
    behavior — cache-aware fires for Anthropic regardless of the flag."""

    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: False
    )
    out = model_factory.maybe_build_cache_aware_anthropic(
        _StubConfig("anthropic"), env={}, gate_on_flag=False
    )
    assert out is _SENTINEL


# ---------------------------------------------------------------------------
# Custom endpoint guard
# ---------------------------------------------------------------------------


def test_custom_endpoint_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A custom ``MAGI_LLM_API_BASE`` may not be honored by the native
    Anthropic client — fall back to LiteLlm rather than silently
    routing to api.anthropic.com."""

    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    out = model_factory.maybe_build_cache_aware_anthropic(
        _StubConfig("anthropic"), env={}, custom_endpoint=True
    )
    assert out is None


# ---------------------------------------------------------------------------
# Build raises → None (robust fallback)
# ---------------------------------------------------------------------------


def test_anthropic_pkg_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch, raises=ModuleNotFoundError("anthropic"))
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    assert (
        model_factory.maybe_build_cache_aware_anthropic(
            _StubConfig("anthropic"), env={}
        )
        is None
    )


def test_arbitrary_build_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch, raises=RuntimeError("boom"))
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    assert (
        model_factory.maybe_build_cache_aware_anthropic(
            _StubConfig("anthropic"), env={}
        )
        is None
    )


# ---------------------------------------------------------------------------
# Credential backfill: config.api_key → os.environ["ANTHROPIC_API_KEY"]
# (only when absent — preserves pre-E-7 byte-identical behavior).
# ---------------------------------------------------------------------------


def test_anthropic_api_key_backfilled_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    model_factory.maybe_build_cache_aware_anthropic(
        _StubConfig("anthropic", api_key="sk-from-config"), env={}
    )
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-from-config"


def test_anthropic_api_key_preserved_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.runtime import model_factory

    _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-preset-by-user")
    model_factory.maybe_build_cache_aware_anthropic(
        _StubConfig("anthropic", api_key="sk-from-config"), env={}
    )
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-preset-by-user"


# ---------------------------------------------------------------------------
# Meta-test: no module outside the factory + the definition file may call
# ``build_cache_aware_claude`` directly. This is the seam lock — preventing
# CLI/shadow/future surfaces from re-introducing a parallel decision path.
# ---------------------------------------------------------------------------


def test_only_factory_and_definition_call_build_cache_aware_claude() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists(), f"package root not found near {package_root}"

    # Allowlist: the cache-aware builder lives in ``anthropic_cache_model.py``
    # (definition) and the single seam in ``runtime/model_factory.py``.
    # Test files may reference it freely (they're outside this gate).
    allowed = {"anthropic_cache_model.py", "model_factory.py"}

    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        rel = path.relative_to(package_root)
        if path.name in allowed:
            continue
        # Skip tests directories inside the package (e.g. cli/tests/).
        if "tests" in rel.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Forbid both the bare call site and the symbol import.
        if "build_cache_aware_claude(" in text:
            offenders.append(str(rel))
    assert offenders == [], (
        "Modules outside runtime/model_factory.py and "
        "adk_bridge/anthropic_cache_model.py called build_cache_aware_claude "
        "directly. Route through "
        "runtime/model_factory.maybe_build_cache_aware_anthropic instead "
        f"(E-7 seam). Offenders: {offenders}"
    )


def test_factory_module_imports_real_builder() -> None:
    """Locked-in dependency: the factory really binds to the cache-aware
    builder from ``adk_bridge.anthropic_cache_model`` (not a fake), so
    the seam means what it says at runtime."""

    from magi_agent.adk_bridge import anthropic_cache_model
    from magi_agent.runtime import model_factory

    assert (
        model_factory.build_cache_aware_claude
        is anthropic_cache_model.build_cache_aware_claude
    )


def test_cli_real_runner_delegates_to_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI shim still works after the refactor: it must surface the
    same return value as the factory."""

    from magi_agent.cli import real_runner
    from magi_agent.runtime import model_factory

    calls = _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: True
    )
    monkeypatch.setattr(
        real_runner, "_model_api_base_kwargs", lambda env=None: {}, raising=False
    )

    from magi_agent.cli.providers import ProviderConfig

    out = real_runner._maybe_build_cache_aware_anthropic(
        ProviderConfig(provider="anthropic", model="claude-sonnet-4-6", api_key="sk"),
        env={},
    )
    assert out is _SENTINEL
    assert calls["model"] == "claude-sonnet-4-6"


def test_shadow_boundary_uses_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hosted shadow path's Anthropic branch routes through the
    factory with ``gate_on_flag=False`` (unconditional for Anthropic, as
    today) — locking it as the single seam."""

    from magi_agent.runtime import model_factory
    from magi_agent.shadow import gate5b4c3_live_runner_boundary as shadow

    calls = _patch_build(monkeypatch)
    monkeypatch.setattr(
        model_factory, "is_message_cache_enabled", lambda env=None: False
    )  # gate would be OFF but shadow must still build
    out = shadow._gate1a_correlated_model_or_label(
        provider_label="anthropic",
        model_label="claude-opus-4-8",
        context=None,
        proxy_url=None,
    )
    assert out is _SENTINEL
    assert calls["model"] == "claude-opus-4-8"
