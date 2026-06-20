"""C-12 regression: model_tiers label-safety regexes consolidated to ONE.

Prior to C-12, ``magi_agent/runtime/model_tiers.py`` shipped two byte-identical
unsafe-label regexes (``_UNSAFE_LABEL_RE`` and ``_UNSAFE_MODEL_LABEL_RE``) that
differed only by including ``/`` in the rejected char class. The generic secret
alternation (``^sk-|^xox[a-z]-|^gh[opusr]_|^github_pat_|^AIza|\\bbearer\\b|
api_key|secret|token|password|private_key``) duplicated the C-1 redaction
kernel.

C-12 consolidates to:
  * ONE label-shape regex (whitespace / shell-metachars / ``..`` / ``~`` /
    ``://``) — slash-allowed at the regex level; provider and capability paths
    reject slash through their secondary ``_PROVIDER_RE.fullmatch`` /
    plain-label constraints.
  * Generic secret vocabulary delegated to
    :func:`magi_agent.ops.safety.contains_secret_marker` (the C-1 kernel — a
    strict superset of the pre-C-12 alternation).

The provider path is asserted to ACCEPT slash-free safe labels and REJECT any
shape containing a slash. The model path is asserted to ACCEPT well-formed
``provider/family`` slash-bearing labels. Both paths uniformly reject the
C-1-kernel secret families.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.runtime.model_tiers import (
    ModelTierRegistry,
    _UNSAFE_LABEL_RE,
    _validate_model,
    _validate_provider,
)


# -- Single-regex invariant ---------------------------------------------------


def test_only_one_unsafe_label_regex_remains() -> None:
    """The pre-C-12 module exported two regexes; C-12 collapses to one."""

    from magi_agent.runtime import model_tiers as _mt

    assert _UNSAFE_LABEL_RE is _mt._UNSAFE_LABEL_RE
    assert not hasattr(_mt, "_UNSAFE_MODEL_LABEL_RE"), (
        "C-12: ``_UNSAFE_MODEL_LABEL_RE`` should be folded into ``_UNSAFE_LABEL_RE``."
    )


# -- Label-shape acceptance / rejection --------------------------------------


@pytest.mark.parametrize(
    "label",
    (
        "openai",
        "anthropic",
        "google",
        "fireworks",
        "openrouter",
    ),
)
def test_provider_validator_accepts_simple_labels(label: str) -> None:
    assert _validate_provider(label) == label


@pytest.mark.parametrize(
    "label",
    (
        "openai/gpt-5",
        "https://provider.example",
        " spaced label ",
        "provider'with'quote",
        "provider|pipe",
        "provider>redirect",
        "provider with space",
    ),
)
def test_provider_validator_rejects_slashes_urls_and_shell_metachars(
    label: str,
) -> None:
    with pytest.raises(ValueError):
        _validate_provider(label)


@pytest.mark.parametrize(
    "label",
    (
        "gpt-5",
        "gemini-3.5-flash",
        "claude-opus-4-8",
        "openai/gpt-5",  # multi-segment model is OK at the model path.
        "google/gemini-3.1-pro",
        "kimi-k2p6",
    ),
)
def test_model_validator_accepts_well_formed_labels(label: str) -> None:
    assert _validate_model(label) == label


@pytest.mark.parametrize(
    "label",
    (
        "../escape",
        "~/.config",
        "shell;injection",
        'quote"injection',
        "model with space",
        "abs|pipe",
    ),
)
def test_model_validator_rejects_shell_metachars_and_traversal(label: str) -> None:
    with pytest.raises(ValueError):
        _validate_model(label)


# -- Kernel-anchored secret rejection (folded from the pre-C-12 alternation) --


# Assembled from fragments so secret scanners don't flag this source file.
def _t(*parts: str) -> str:
    return "".join(parts)


_SK_LABEL = _t("sk-", "abcdEFGH1234")
_XOXB_LABEL = _t("xoxb-", "abcdEFGH1234")
_GHP_LABEL = _t("ghp_", "abcdEFGH1234")
_GH_PAT_LABEL = _t("github_pat_", "abcdEFGH1234")
_AIZA_LABEL = _t("AIza", "abcdEFGH1234")


@pytest.mark.parametrize(
    "label",
    (
        _SK_LABEL,
        _XOXB_LABEL,
        _GHP_LABEL,
        _GH_PAT_LABEL,
        _AIZA_LABEL,
        "bearer",
        "api_key",
        "apikey",
        "api-key",
        "secret",
        "token",
        "password",
        "private_key",
        "private-key",
    ),
)
def test_secret_shaped_labels_rejected_by_both_paths(label: str) -> None:
    """The C-1 kernel (``contains_secret_marker``) catches every pre-C-12
    generic secret-pattern shape uniformly — both provider and model paths
    reject them."""

    with pytest.raises(ValueError):
        _validate_provider(label)
    with pytest.raises(ValueError):
        _validate_model(label)


def test_secret_shaped_labels_rejected_via_registry_resolve() -> None:
    """End-to-end: ``ModelTierRegistry.resolve(provider=..., model=...)`` raises
    on secret-shaped labels for either argument (post-C-12, both paths share the
    kernel secret vocabulary)."""

    registry = ModelTierRegistry.with_defaults()
    with pytest.raises(ValidationError):
        registry.resolve(provider=_SK_LABEL, model="gpt-5")
    with pytest.raises(ValidationError):
        registry.resolve(provider="openai", model=_GHP_LABEL)
