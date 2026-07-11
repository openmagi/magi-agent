"""User-visible generation request, identity, and history contract builders.

Pure move out of ``magi_agent/transport/chat.py`` (08-PR1). Builds the
``UserVisibleGenerationRequest`` envelope for the user-visible serving
path plus its sanitization helpers: last-user-text/image extraction, bounded
sanitized recent history, public identity policy, and the model-visible canary
runner request. Behavior is unchanged; ``transport.chat`` re-exports these
names for compatibility.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from pathlib import Path
import re
import time
import uuid
from typing import Any

from magi_agent.gates.gate1a_readonly_tools import Gate1AReadOnlyToolBundle
from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolBundle
# reuse the established image sanitizer; message_builder exposes no public image API
from magi_agent.runtime.message_builder import _collect_image_blocks
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.session_identity import session_key_from_headers
from magi_agent.runtime.skill_slash import (
    SkillSlashActivation,
    SkillSlashMiss,
    resolve_skill_slash,
)
from magi_agent.runtime.user_visible_model_routing import (
    _select_user_visible_model_route,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationRequest,
)
from magi_agent.transport.chat_shared import (
    Gate5BUserVisibleChatRouteConfig,
    _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE,
    _bounded_public_text,
    _is_sha256_digest,
    _public_safe_context_continuity_metadata,
    _route_tool_bundle_full,
    _route_tool_bundle_ready,
    _sha256_digest,
)

# Staged rename (08-PR2): the envelope is the first-party user-visible
# generation contract, not a shadow diagnostic. New code should use this
# alias; the Gate5B4C3* class name remains until the physical rename of the
# contract module lands (wire schemaVersion is unchanged either way).
UserVisibleGenerationRequest = Gate5B4C3ShadowGenerationRequest

_logger = logging.getLogger(__name__)

_APP_CHANNEL_HISTORY_SCHEMA = "openmagi.app_channel_history.v1"


_PUBLIC_IDENTITY_POLICY = {
    "schemaVersion": "gate5b.publicIdentityPolicy.v1",
    "canonicalName": "Magi Agent",
    "platformName": "OpenMagi",
    "modelVisibleSystemContext": (
        "You are Magi Agent for OpenMagi. Present the user-visible assistant "
        "identity as Magi Agent / OpenMagi, and keep infrastructure, package, "
        "namespace, deployment, and runtime implementation names out of "
        "model-visible public identity context."
    ),
}


_LEGACY_IDENTITY_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\bmagi\s+agent\b", re.IGNORECASE),
        "Magi Agent",
        "legacy_public_identity_normalized",
    ),
    (
        re.compile(r"\bmagi[-_]agent\b|\bmagi-core-agent\b", re.IGNORECASE),
        "OpenMagi runtime",
        "legacy_runtime_identity_normalized",
    ),
)


_MODEL_VISIBLE_CONTEXT_MAX_CHARS = 1_000_000


def _resolve_activated_skill_payload(
    user_text: str,
    *,
    workspace_root: Path,
    max_body_chars: int,
) -> dict[str, object] | None:
    """Resolve a leading /skill-name to a contract-shaped activatedSkill payload.

    Fail-open: any resolver error is swallowed (logged) and None is returned so
    a broken skill file can never break chat. Returns None for a non-slash
    message or a reserved command word.
    """
    try:
        resolved = resolve_skill_slash(
            user_text,
            workspace_root=workspace_root,
            max_body_chars=max_body_chars,
        )
    except Exception:  # noqa: BLE001 - fail-open by design
        _logger.warning("slash-to-skill resolution failed; proceeding without activation", exc_info=True)
        return None
    if resolved is None:
        return None
    if isinstance(resolved, SkillSlashMiss):
        return {
            "skillName": "",
            "invokedToken": resolved.invoked_token,
            "sourcePath": "",
            "source": "",
            "body": "",
            "bodyDigest": _sha256_digest(""),
            "truncated": False,
            "miss": True,
            "nearMatches": list(resolved.near_matches),
        }
    if isinstance(resolved, SkillSlashActivation):
        return {
            "skillName": resolved.skill_name,
            "invokedToken": resolved.invoked_token,
            "sourcePath": resolved.source_path,
            "source": resolved.source,
            "body": resolved.body,
            "bodyDigest": _sha256_digest(resolved.body),
            "truncated": resolved.truncated,
            "miss": False,
            "nearMatches": [],
        }
    return None


def _build_user_visible_generation_request(
    *,
    runtime: OpenMagiRuntime,
    route_config: Gate5BUserVisibleChatRouteConfig,
    generation_config: Gate5B4C3ShadowGenerationConfig,
    payload: object,
    trace_id: str | None,
    canary_request_digest: str | None = None,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle | None = None,
    request_headers: Mapping[str, str] | None = None,
    slash_skill_activation_enabled: bool = False,
    slash_skill_workspace_root: Path | None = None,
    slash_skill_body_max_chars: int = 32000,
) -> UserVisibleGenerationRequest:
    if not isinstance(payload, Mapping):
        raise ValueError("chat payload must be an object")
    user_text = _extract_last_user_text(payload)
    if not user_text:
        raise ValueError("chat payload must contain a user message")
    image_blocks = _extract_last_user_image_blocks(payload)
    tool_bundle_ready = _route_tool_bundle_ready(gate1a_bundle)
    full_toolhost_ready = _route_tool_bundle_full(gate1a_bundle)
    history_messages = _build_gate5b_sanitized_recent_history(
        payload,
        max_messages=(
            generation_config.approved_budgets.max_sanitized_history_messages
            if full_toolhost_ready
            else 0
        ),
    )
    sanitized_text = _build_gate5b_model_visible_current_turn_text(
        user_text,
        payload=None if history_messages else payload,
    )
    input_digest = _sha256_digest(sanitized_text)
    # The gate5b counter store dedups by request digest. The digest must be
    # keyed on a per-turn identity (not on message content) so two genuinely
    # distinct turns that happen to share identical text do not collide and get
    # the second one silently rejected as ``counter_duplicate_replay``. When the
    # client supplies no turn identity at all we mint a unique nonce, trading
    # away replay dedup we cannot key correctly for the guarantee that a real
    # user turn is never dropped without a response.
    turn_identity = _request_turn_identity(payload, trace_id=trace_id)
    request_seed = "|".join(
        (
            runtime.config.bot_id,
            runtime.config.user_id,
            route_config.environment,
            input_digest,
            turn_identity
            if turn_identity is not None
            else f"nonce:{uuid.uuid4().hex}",
        )
    )
    request_digest = (
        canary_request_digest
        if _is_sha256_digest(canary_request_digest)
        else _sha256_digest(request_seed)
    )
    provider_label, model_label, credential_ref = _select_user_visible_model_route(
        generation_config,
        payload=payload,
        request_headers=request_headers,
    )
    router_digest = _sha256_digest(f"{provider_label}:{model_label}:{request_digest}")
    profile_digest = _sha256_digest("gate5b-user-visible-canary-profile-v1")
    session_key_digest = _session_key_digest_from_request(
        payload,
        request_headers=request_headers,
    )
    tools_policy = (
        "selected_full_toolhost"
        if full_toolhost_ready
        else "shadow_readonly" if tool_bundle_ready else "disabled"
    )
    source_authority = (
        "bounded_sanitized_recent_history"
        if history_messages
        else "current_turn_only"
    )
    now_ms = int(time.time() * 1000)
    turn_payload: dict[str, object] = {
        "turnId": f"turn_{input_digest.removeprefix('sha256:')[:16]}",
        "turnDigest": _sha256_digest(request_seed + ":turn"),
        "sanitizedCurrentTurnText": sanitized_text,
        "sanitizedInputTextDigest": input_digest,
        "channelName": "app_channel",
        "tsResponseCorrelationId": f"ts_{request_digest.removeprefix('sha256:')[:16]}",
    }
    if history_messages:
        turn_payload["sanitizedRecentHistory"] = history_messages
    if image_blocks:
        turn_payload["sanitizedImageBlocks"] = [
            {"mediaType": b["source"]["media_type"], "data": b["source"]["data"]}
            for b in image_blocks
        ]
    # Slash-to-skill activation (A3). Resolved from the RAW user text (which
    # still carries the leading slash), not the sanitized/identity-wrapped text.
    # The user message is never modified; only the optional activatedSkill field
    # is added, which the adapter injects into the system-instruction channel.
    if (
        slash_skill_activation_enabled
        and slash_skill_workspace_root is not None
        and user_text.lstrip().startswith("/")
    ):
        activated_skill = _resolve_activated_skill_payload(
            user_text,
            workspace_root=slash_skill_workspace_root,
            max_body_chars=slash_skill_body_max_chars,
        )
        if activated_skill is not None:
            turn_payload["activatedSkill"] = activated_skill
    redacted_byte_count = len(sanitized_text.encode("utf-8")) + sum(
        len(str(item["sanitizedText"]).encode("utf-8"))
        for item in history_messages
    )
    return UserVisibleGenerationRequest.model_validate(
        {
            "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
            "shadowGenerationId": f"uv_canary_{request_digest.removeprefix('sha256:')[:24]}",
            "requestIdDigest": request_digest,
            "traceIdDigest": _sha256_digest(trace_id or request_digest),
            "createdAt": now_ms,
            "selection": {
                "botIdDigest": _sha256_digest(runtime.config.bot_id),
                "ownerUserIdDigest": _sha256_digest(runtime.config.user_id),
                "environment": route_config.environment,
                "selectedTarget": "gate5b_selected_bot",
                **(
                    {"sessionKeyDigest": session_key_digest}
                    if session_key_digest is not None
                    else {}
                ),
            },
            "turn": turn_payload,
            "modelRouting": {
                "routingSource": "per_turn_injected",
                "providerLabel": provider_label,
                "modelLabel": model_label,
                "routerDecisionDigest": router_digest,
                "routingProfileDigest": profile_digest,
                "shadowCredentialRef": credential_ref,
                "credentialRefSource": "server_config",
                "maxOutputTokens": generation_config.approved_budgets.max_output_tokens,
            },
            "recipeProfile": {
                "recipeId": "gate5b-user-visible-canary",
                "recipeVersion": "v1",
                "profileId": "base-python-text-canary",
                "profileVersion": "v1",
                "runtimeEngine": "adk-python",
                "toolsPolicy": tools_policy,
                "memoryMode": "disabled",
                "sourceAuthority": source_authority,
            },
            "policy": {
                "typeScriptResponseAuthority": True,
                "pythonDiagnosticOnly": True,
                "outputIsolation": "local_diagnostic_only",
                "toolsDisabled": not tool_bundle_ready,
                "toolHostDispatchAllowed": tool_bundle_ready,
                "memoryProviderCallsAllowed": False,
                "memoryWritesAllowed": False,
                "promptMemoryInjectionAllowed": False,
                "workspaceMutationAllowed": False,
                "childExecutionAllowed": False,
                "missionRuntimeAllowed": False,
                "evidenceBlockModeAllowed": False,
            },
            "budgets": generation_config.approved_budgets.model_dump(
                by_alias=True,
                mode="python",
            ),
            "redaction": {
                "sanitizerId": "gate5b-user-visible-canary",
                "sanitizerVersion": "v1",
                "policyId": (
                    "bounded-sanitized-recent-history"
                    if history_messages
                    else "current-turn-only"
                ),
                "status": "passed",
                "redactedAt": now_ms,
                "redactedByteCount": redacted_byte_count,
                "forbiddenFieldScan": "passed",
                "sanitizedPayloadDigest": input_digest,
            },
        }
    )


def _request_turn_identity(
    payload: Mapping[str, object],
    *,
    trace_id: str | None,
) -> str | None:
    """Per-turn idempotency identity supplied by the client, if any.

    The gate5b counter store keys replay protection on the request digest, so
    the digest must reflect *which turn* this is rather than *what was said*.
    A client-supplied ``turnId`` uniquely identifies a logical turn (a genuine
    retry reuses it; distinct turns differ even with identical text); the
    request ``trace_id`` is the next-best per-request signal. ``sessionId`` is
    deliberately excluded because it is per-channel-stable, not per-turn.

    Returns ``None`` when the client supplied no turn identity, signalling the
    caller to mint a unique nonce so a distinct turn is never deduped away.
    """
    turn_id = payload.get("turnId")
    if isinstance(turn_id, str) and turn_id.strip():
        return f"turn:{turn_id.strip()}"
    if trace_id and trace_id.strip():
        return f"trace:{trace_id.strip()}"
    return None


def _session_key_digest_from_request(
    payload: Mapping[str, object],
    *,
    request_headers: Mapping[str, str] | None = None,
) -> str | None:
    for candidate in (
        payload.get("sessionId"),
        session_key_from_headers(request_headers) if request_headers is not None else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return _sha256_digest(candidate.strip())
    return None


def _extract_last_user_text(payload: Mapping[str, object]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for block in content:
                if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
            return "\n".join(chunks)
    return ""


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$")


def _normalize_image_block(block: object) -> dict | None:
    if not isinstance(block, Mapping):
        return None
    block_type = block.get("type")
    if block_type == "image":
        return dict(block)  # already Anthropic-style; sanitized downstream
    if block_type == "image_url":
        image_url = block.get("image_url")
        url = image_url.get("url") if isinstance(image_url, Mapping) else None
        if not isinstance(url, str):
            return None
        match = _DATA_URL_RE.match(url.strip())
        if not match:
            return None
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": match.group("mime"),
                "data": match.group("data"),
            },
        }
    return None


def _extract_last_user_image_blocks(payload: Mapping[str, object]) -> list[dict[str, object]]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            return []
        normalized = [
            nb for nb in (_normalize_image_block(b) for b in content) if nb is not None
        ]
        return _collect_image_blocks({"imageBlocks": normalized}, {})
    return []


def _build_gate5b_sanitized_recent_history(
    payload: Mapping[str, object],
    *,
    max_messages: int,
) -> tuple[dict[str, str], ...]:
    if max_messages <= 0:
        return ()
    projected: list[dict[str, str]] = list(_app_channel_history_messages(payload))
    source_messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if not isinstance(source_messages, list):
        return _dedupe_latest_history(projected, max_messages=max_messages)
    last_user_index: int | None = None
    for index, item in enumerate(source_messages):
        if isinstance(item, Mapping) and item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        for item in source_messages[:last_user_index]:
            if not isinstance(item, Mapping):
                continue
            message = _sanitized_history_message(
                role=item.get("role"),
                content=item.get("content"),
            )
            if message is not None:
                projected.append(message)
    return _dedupe_latest_history(projected, max_messages=max_messages)


def _app_channel_history_messages(
    payload: Mapping[str, object],
) -> tuple[dict[str, str], ...]:
    channel_history = payload.get("channelHistory")
    if not isinstance(channel_history, Mapping):
        return ()
    if channel_history.get("schema") != _APP_CHANNEL_HISTORY_SCHEMA:
        return ()
    raw_messages = channel_history.get("messages")
    if not isinstance(raw_messages, list):
        return ()
    projected: list[dict[str, str]] = []
    for item in raw_messages:
        if not isinstance(item, Mapping):
            continue
        message = _sanitized_history_message(
            role=item.get("role"),
            content=item.get("content"),
        )
        if message is not None:
            projected.append(message)
    return tuple(projected)


def _sanitized_history_message(
    *,
    role: object,
    content: object,
) -> dict[str, str] | None:
    role_text = str(role or "").strip().lower()
    if role_text == "system":
        role_text = "assistant"
    if role_text not in {"user", "assistant"}:
        return None
    text = _message_content_to_text(content)
    text = _RUNNER_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE.sub("[redacted]", text)
    text = sanitize_gate5b_model_visible_identity_text(text)
    bounded = text[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
    if not bounded:
        return None
    return {
        "role": role_text,
        "sanitizedText": bounded,
        "sanitizedTextDigest": _sha256_digest(bounded),
    }


def _dedupe_latest_history(
    messages: Sequence[Mapping[str, str]],
    *,
    max_messages: int,
) -> tuple[dict[str, str], ...]:
    if max_messages <= 0:
        return ()
    seen: set[tuple[str, str]] = set()
    selected_reversed: list[dict[str, str]] = []
    for item in reversed(messages):
        role = str(item.get("role") or "")
        digest = str(item.get("sanitizedTextDigest") or "")
        key = (role, digest)
        if key in seen:
            continue
        seen.add(key)
        selected_reversed.append(dict(item))
        if len(selected_reversed) >= max_messages:
            break
    return tuple(reversed(selected_reversed))


def build_public_identity_policy() -> dict[str, str]:
    return dict(_PUBLIC_IDENTITY_POLICY)


def sanitize_gate5b_model_visible_identity_text(value: object) -> str:
    text, _signals = _normalize_gate5b_model_visible_identity_text(value)
    return text


def build_gate5b_user_visible_canary_runner_request(
    payload: Mapping[str, Any],
    *,
    context_continuity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    messages: list[dict[str, str]] = []
    signals: list[str] = []

    source_messages = payload.get("messages") if isinstance(payload, Mapping) else None
    projected_messages: list[dict[str, str]] = []
    for item in _app_channel_history_messages(payload):
        projected_messages.append(
            {"role": item["role"], "content": item["sanitizedText"]}
        )
    if isinstance(source_messages, list):
        for item in source_messages:
            if not isinstance(item, Mapping):
                continue
            role = _safe_chat_role(item.get("role"))
            content, content_signals = _normalize_gate5b_model_visible_identity_text(
                _message_content_to_text(item.get("content"))
            )
            _extend_unique(signals, content_signals)
            bounded = content[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
            if bounded:
                projected_messages.append({"role": role, "content": bounded})
        messages.extend(_latest_model_visible_messages(projected_messages))

    workspace_identity_context: list[str] = []
    for key in (
        "workspaceIdentityText",
        "workspace_identity_text",
        "identityText",
        "identity_text",
    ):
        if key not in payload:
            continue
        content, content_signals = _normalize_gate5b_model_visible_identity_text(payload[key])
        _extend_unique(signals, content_signals)
        bounded = content[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
        if bounded:
            workspace_identity_context.append(bounded)

    request: dict[str, object] = {
        "schemaVersion": "gate5b.userVisibleCanaryRunnerRequest.v1",
        "publicIdentity": build_public_identity_policy(),
        "messages": tuple(messages),
        "workspaceIdentityContext": tuple(workspace_identity_context),
        "legacyIdentitySignals": tuple(signals),
        "limits": {
            "maxModelVisibleContextChars": _MODEL_VISIBLE_CONTEXT_MAX_CHARS,
        },
    }
    safe_continuity = _public_safe_context_continuity_metadata(context_continuity)
    if safe_continuity is not None:
        request["contextContinuity"] = safe_continuity
    return request


def _latest_model_visible_messages(
    messages: Sequence[Mapping[str, str]],
    *,
    limit: int = 16,
) -> tuple[dict[str, str], ...]:
    if len(messages) <= limit:
        return tuple(dict(item) for item in messages)
    system_messages = [dict(item) for item in messages if item.get("role") == "system"]
    conversation_messages = [
        dict(item) for item in messages if item.get("role") != "system"
    ]
    selected_system = system_messages[: min(len(system_messages), 2)]
    remaining = max(1, limit - len(selected_system))
    return tuple([*selected_system, *conversation_messages[-remaining:]])


def _build_gate5b_model_visible_current_turn_text(
    user_text: str,
    *,
    payload: Mapping[str, object] | None = None,
) -> str:
    identity = _PUBLIC_IDENTITY_POLICY["modelVisibleSystemContext"]
    sanitized_user_text = sanitize_gate5b_model_visible_identity_text(
        _bounded_public_text(user_text, max_chars=_MODEL_VISIBLE_CONTEXT_MAX_CHARS)
    )
    projected_messages: list[dict[str, str]] = []
    source_messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if isinstance(source_messages, list):
        for item in source_messages:
            if not isinstance(item, Mapping):
                continue
            content = sanitize_gate5b_model_visible_identity_text(
                _message_content_to_text(item.get("content"))
            )
            bounded = content[:_MODEL_VISIBLE_CONTEXT_MAX_CHARS].strip()
            if bounded:
                projected_messages.append(
                    {"role": _safe_chat_role(item.get("role")), "content": bounded}
                )
    visible_messages = _latest_model_visible_messages(projected_messages)
    if visible_messages:
        conversation = "\n".join(
            f"{item['role']}: {item['content']}" for item in visible_messages
        )
        text = (
            f"{identity}\n\n"
            f"Recent visible conversation:\n{conversation}\n\n"
            f"Current user message:\n{sanitized_user_text}"
        )
    else:
        text = f"{identity}\n\nUser message:\n{sanitized_user_text}"
    return _bounded_public_text(text, max_chars=_MODEL_VISIBLE_CONTEXT_MAX_CHARS)


def _message_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _normalize_gate5b_model_visible_identity_text(value: object) -> tuple[str, tuple[str, ...]]:
    text = _message_content_to_text(value)
    signals: list[str] = []
    for pattern, replacement, signal in _LEGACY_IDENTITY_PATTERNS:
        if not pattern.search(text):
            continue
        text = pattern.sub(replacement, text)
        if signal not in signals:
            signals.append(signal)
    return text, tuple(signals)


def _safe_chat_role(value: object) -> str:
    role = str(value or "user").strip().lower()
    if role in {"system", "user", "assistant"}:
        return role
    return "user"


def _extend_unique(target: list[str], values: tuple[str, ...]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
