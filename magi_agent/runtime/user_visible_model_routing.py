"""User-visible model-route selection policy for the Gate5B serving path.

Pure move out of ``magi_agent/transport/chat.py`` (08-PR1): model routing is
policy, not transport, so it lives under ``magi_agent.runtime``. Behavior is
unchanged; ``transport.chat`` re-exports these names for compatibility.
"""

from __future__ import annotations

from collections.abc import Mapping
import re

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
)

_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

def _safe_label_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_LABEL_RE.match(text) else None

def _single_config_value(values: tuple[str, ...]) -> str:
    if len(values) != 1:
        raise ValueError("Gate 5B user-visible canary requires one configured value")
    return values[0]

def _select_user_visible_model_route(
    generation_config: Gate5B4C3ShadowGenerationConfig,
    *,
    payload: Mapping[str, object],
    request_headers: Mapping[str, str] | None,
) -> tuple[str, str, str]:
    if not generation_config.allowed_model_routes:
        return (
            _single_config_value(generation_config.allowed_provider_labels),
            _single_config_value(generation_config.allowed_model_labels),
            _single_config_value(generation_config.allowed_shadow_credential_refs),
        )
    allowed_routes = tuple(
        tuple(route.split(":", 1))
        for route in generation_config.allowed_model_routes
        if route.count(":") == 1
    )
    requested_provider, requested_model = _requested_user_visible_model_route(
        payload=payload,
        request_headers=request_headers,
    )
    if requested_model is not None:
        candidates = tuple(
            (provider, model)
            for provider, model in allowed_routes
            if model == requested_model
            and (requested_provider is None or provider == requested_provider)
        )
        if len(candidates) != 1:
            raise ValueError("requested model route is not allowlisted")
        provider_label, model_label = candidates[0]
    else:
        if not allowed_routes:
            raise ValueError("Gate 5B user-visible canary requires model routes")
        provider_label, model_label = allowed_routes[0]
    credential_ref = _credential_ref_for_user_visible_provider(
        generation_config,
        provider_label=provider_label,
    )
    return provider_label, model_label, credential_ref

def _requested_user_visible_model_route(
    *,
    payload: Mapping[str, object],
    request_headers: Mapping[str, str] | None,
) -> tuple[str | None, str | None]:
    headers = request_headers or {}
    header_provider = _safe_label_or_none(
        headers.get("x-magi-runtime-provider")
        or headers.get("x-magi-router-provider")
    )
    header_model = _safe_label_or_none(
        headers.get("x-magi-runtime-model")
        or headers.get("x-magi-router-model")
    )
    if header_model is not None:
        return header_provider, header_model
    model_routing = payload.get("modelRouting")
    if isinstance(model_routing, Mapping):
        routed_provider = _safe_label_or_none(
            model_routing.get("providerLabel")
            or model_routing.get("provider")
            or model_routing.get("perTurnProvider")
        )
        routed_model = _safe_label_or_none(
            model_routing.get("modelLabel")
            or model_routing.get("model")
            or model_routing.get("perTurnModel")
        )
        if routed_model is not None:
            return routed_provider, routed_model
    return _provider_model_from_user_visible_model(payload.get("model"))

def _provider_model_from_user_visible_model(value: object) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    lowered = text.lower()
    if lowered in {"auto", "openclaw"} or lowered.endswith("/auto"):
        return None, None
    separator = "/" if "/" in text else ":" if ":" in text else ""
    if separator:
        provider, model = (part.strip() for part in text.split(separator, 1))
    else:
        provider, model = "", text
    safe_provider = _safe_label_or_none(provider) if provider else None
    safe_model = _safe_label_or_none(model)
    return safe_provider, safe_model

def _credential_ref_for_user_visible_provider(
    generation_config: Gate5B4C3ShadowGenerationConfig,
    *,
    provider_label: str,
) -> str:
    for binding in generation_config.provider_credential_bindings:
        if binding.provider_label == provider_label:
            return binding.credential_ref
    if len(generation_config.allowed_shadow_credential_refs) == 1:
        return generation_config.allowed_shadow_credential_refs[0]
    raise ValueError("Gate 5B user-visible canary requires a provider credential binding")
