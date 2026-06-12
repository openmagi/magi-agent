"""Model-backed channel-workflow classifier seam (C5, doc 03 PR-5).

The channel dynamic-workflow code is fully merged and double-gated
(``MAGI_WORKFLOW_EXECUTOR_ENABLED`` + ``MAGI_CHANNEL_WORKFLOWS_ENABLED``).
The remaining gap is that the *default* classifier wired into
``streaming_chat_route`` has no model behind it, so ``aclassify`` always
returns ``"general"`` — auto-detect is inert and only the explicit ``/research``
prefix path engages a workflow.

This module closes that gap with a single seam:

    build_live_classifier_if_configured() -> TaskKindClassifier

When a provider is configured (a key is resolvable from ``MAGI_CONFIG`` /
``~/.magi/config.toml`` / provider env vars), the returned classifier carries a
``model_factory`` so ``aclassify`` performs a real (cheap) classification call.
When nothing is configured the function returns the inert
:class:`TaskKindClassifier` (byte-identical to today — auto-detect stays
``"general"``). No new feature flag is introduced: the classifier becomes live
purely on the presence of a configured model, mirroring the rest of the CLI.

Boundary discipline
-------------------
This module imports **no** HTTP/SMTP client at module scope. The actual model is
built lazily, inside the ``model_factory`` closure, by reusing
``readonly_classifier._build_litellm_for_config`` (which itself defers the
``LiteLlm`` import). Tests inject fakes and never touch the network.

Model selection
---------------
``MAGI_WORKFLOW_CLASSIFIER_MODEL`` (optional) overrides the model id used for
classification, letting an operator point auto-detect at a cheap/fast model
(e.g. a Haiku-class model) without changing the main turn model. When unset the
configured provider's default model is used.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from magi_agent.channels.taskkind_classifier import TaskKindClassifier

__all__ = ["build_live_classifier_if_configured"]

# Optional model override so auto-detect can run on a cheaper/faster model than
# the main turn. Mirrors readonly_classifier's MAGI_SMART_APPROVE_MODEL seam.
_ENV_MODEL_OVERRIDE = "MAGI_WORKFLOW_CLASSIFIER_MODEL"


def _resolve_provider_config() -> object | None:
    """Return a resolved ``ProviderConfig`` or ``None`` if nothing is configured.

    Thin wrapper around ``cli.providers.resolve_provider_config`` so tests can
    monkeypatch this single seam without touching the config/env machinery.
    The import is local to keep this module import-light.
    """
    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    return resolve_provider_config()


def _build_model_for_config(config: object) -> object:
    """Build a LiteLlm-compatible model from ``config``; may raise.

    Reuses the SmartApprove model builder, honouring
    ``MAGI_WORKFLOW_CLASSIFIER_MODEL`` as the per-classifier model override. The
    ``LiteLlm`` import is deferred inside that helper, so this module stays
    import-clean of HTTP clients.
    """
    from magi_agent.cli.readonly_classifier import (  # noqa: PLC0415
        _build_litellm_for_config,
    )

    override = os.environ.get(_ENV_MODEL_OVERRIDE, "").strip() or None
    return _build_litellm_for_config(config, model_override=override)


def build_live_classifier_if_configured(
    *,
    model_factory: Callable[[], object] | None = None,
) -> TaskKindClassifier:
    """Return a channel-workflow classifier, model-backed when configured.

    Parameters
    ----------
    model_factory:
        Optional explicit zero-arg model factory (test seam / hosted override).
        When provided it takes precedence over provider resolution.

    Behaviour
    ---------
    - An explicit ``model_factory`` → a model-backed classifier using it.
    - Else if a provider is configured → a model-backed classifier whose factory
      lazily builds the model from that provider config (honouring
      ``MAGI_WORKFLOW_CLASSIFIER_MODEL``). If building the model later raises, the
      classifier itself fails safe to ``"general"`` (``TaskKindClassifier``
      contract), so auto-detect degrades to today's inert behaviour.
    - Else (nothing configured / resolution error) → the inert
      ``TaskKindClassifier()`` (``"general"`` until a model is wired).

    Never raises: provider resolution errors collapse to the inert classifier.
    """
    if model_factory is not None:
        return TaskKindClassifier(model_factory=model_factory)

    try:
        config = _resolve_provider_config()
    except Exception:  # noqa: BLE001 — never break chat wiring; degrade to inert
        config = None

    if config is None:
        return TaskKindClassifier()

    def _factory() -> object:
        # _build_model_for_config may raise; TaskKindClassifier._resolve_model
        # catches that and resolves to "general" (fail-safe, never raises here).
        return _build_model_for_config(config)

    return TaskKindClassifier(model_factory=_factory)
