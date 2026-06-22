"""E-13 — single source of truth for ``ProviderFamily`` + ``detect_provider_family``.

Both used to live in ``prompt/provider_adapter.py`` (the prompt-layer
package), which is the wrong home: tool-schema repair (E-12) and the
cache injector (E-7) both consume them too. This module locks the
no-fork invariant after the consolidation: the canonical home is
``magi_agent.shared.provider_family``, and ``prompt/provider_adapter``
re-exports for back-compat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.prompt.injection import detect_provider
from magi_agent.shared.provider_family import (
    ProviderFamily,
    detect_provider_family,
)


# ---------------------------------------------------------------------------
# Parity: detect_provider() and detect_provider_family().value agree across
# the canonical model table. Fireworks/Default extend the string form.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected_provider,expected_family",
    [
        # Anthropic
        ("claude-sonnet-4-6", "anthropic", ProviderFamily.ANTHROPIC),
        ("claude-opus-4-8", "anthropic", ProviderFamily.ANTHROPIC),
        ("anthropic/claude-haiku-4-5", "anthropic", ProviderFamily.ANTHROPIC),
        # OpenAI
        ("gpt-5.5", "openai", ProviderFamily.OPENAI),
        ("openai/gpt-5.5", "openai", ProviderFamily.OPENAI),
        ("openai-codex/gpt-5.5", "openai", ProviderFamily.OPENAI),
        # Google
        ("gemini-3.5-flash", "google", ProviderFamily.GOOGLE),
        ("google/gemini-3.1-pro-preview", "google", ProviderFamily.GOOGLE),
        # Router-wrapped substring fallback for detect_provider
        ("some-router/claude-flex", "anthropic", ProviderFamily.ANTHROPIC),
        # Fireworks: detect_provider returns "unknown" (it predates fireworks);
        # detect_provider_family extends to FIREWORKS via prefix detection.
        ("kimi-k2p6", "unknown", ProviderFamily.FIREWORKS),
        ("fireworks/kimi-k2p6", "unknown", ProviderFamily.FIREWORKS),
        ("minimax-m2p7", "unknown", ProviderFamily.FIREWORKS),
        # Default fallback
        ("magi-smart-router/auto", "unknown", ProviderFamily.DEFAULT),
        ("totally-unknown-model-v0", "unknown", ProviderFamily.DEFAULT),
    ],
)
def test_detect_consistency(
    model: str, expected_provider: str, expected_family: ProviderFamily
) -> None:
    assert detect_provider(model) == expected_provider
    assert detect_provider_family(model) == expected_family


# ---------------------------------------------------------------------------
# Re-export identity: importing ProviderFamily / detect_provider_family from
# the legacy prompt/provider_adapter path must yield the SAME object as the
# new shared home. Prevents a re-fork by tab-completion.
# ---------------------------------------------------------------------------


def test_prompt_re_export_is_same_class_object() -> None:
    from magi_agent.prompt.provider_adapter import (
        ProviderFamily as ProviderFamilyFromPrompt,
    )

    assert ProviderFamilyFromPrompt is ProviderFamily


def test_prompt_re_export_is_same_function_object() -> None:
    from magi_agent.prompt.provider_adapter import (
        detect_provider_family as detect_provider_family_from_prompt,
    )

    assert detect_provider_family_from_prompt is detect_provider_family


def test_prompt_package_re_export_is_same_class_object() -> None:
    from magi_agent.prompt import ProviderFamily as ProviderFamilyFromPackage

    assert ProviderFamilyFromPackage is ProviderFamily


# ---------------------------------------------------------------------------
# Single-definition meta-test: only ``shared/provider_family.py`` may carry
# the literal ``class ProviderFamily(str, Enum):`` definition. Re-exports
# elsewhere are fine (they're imports, not definitions).
# ---------------------------------------------------------------------------


def test_only_shared_module_defines_provider_family() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    canonical = {"provider_family.py"}
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        if "tests" in path.relative_to(package_root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "class ProviderFamily(str, Enum)" in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Second definition of ``class ProviderFamily(str, Enum):`` outside "
        "``shared/provider_family.py``. Re-exports must use ``from "
        "magi_agent.shared.provider_family import ProviderFamily``. "
        f"Offenders: {offenders}"
    )


def test_only_shared_module_defines_detect_provider_family() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    canonical = {"provider_family.py"}
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        if "tests" in path.relative_to(package_root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "def detect_provider_family(" in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Second definition of ``detect_provider_family`` outside "
        "``shared/provider_family.py``. "
        f"Offenders: {offenders}"
    )
