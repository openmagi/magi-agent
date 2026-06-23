"""HTTP hook executor — POSTs (or configurable method) to an external webhook URL.

Protocol
--------
- JSON body: same sanitized payload as ``CommandHookExecutor``
- Custom headers from ``manifest.http_headers``
- Timeout via ``httpx.AsyncClient(timeout=manifest.timeout_ms / 1000)``
- TLS verification on by default; set ``MAGI_HOOK_HTTP_VERIFY_TLS=false`` to
  disable (development / self-signed cert environments only).

Response handling
-----------------
- ``200``        — parse JSON body as HookResult fields
- ``204``        — no-op, return ``continue``
- ``4xx``        — log warning; return ``continue`` (fail-open) or ``block``
                   (fail-closed) depending on ``manifest.fail_open``
- ``5xx``        — log error; same fail-open / fail-closed policy
- Timeout        — same as error
- Malformed JSON — log warning, return ``continue``

Security notes
--------------
- No auth tokens are injected into the request body or default headers.  If the
  webhook endpoint requires authentication, the operator must supply the
  ``Authorization`` header (or equivalent) via ``manifest.http_headers``.
- The body payload is sanitized by ``_build_sanitized_hook_input`` (paths,
  secrets, thinking blocks all redacted).
- TLS verification is on by default.  ``MAGI_HOOK_HTTP_VERIFY_TLS=false`` is
  intentionally dev-only — never set it in production.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from magi_agent.hooks.context import HookContext
from magi_agent.hooks.executors import _REGISTRY
from magi_agent.hooks.executors.sanitize import _build_sanitized_hook_input, _sanitize_value
from magi_agent.hooks.manifest import HookManifest
from magi_agent.hooks.result import HookResult

__all__ = ["HttpHookExecutor"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TLS verification control
# ---------------------------------------------------------------------------

def _tls_verify() -> bool:
    """Return True unless ``MAGI_HOOK_HTTP_VERIFY_TLS=false`` is set (case-insensitive)."""
    # I-4: routed through the typed flag registry. Registered as
    # ``str`` because the default-TRUE-when-unset + literal-only-disable
    # semantics differ from ``flag_bool``'s strict default-OFF.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    raw = (flag_str("MAGI_HOOK_HTTP_VERIFY_TLS") or "true").strip().lower()
    return raw != "false"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_http_response_body(body: str, hook_name: str) -> HookResult:
    """Parse the JSON body from a 200 HTTP response into a ``HookResult``.

    Mirrors the logic in ``_parse_hook_output`` from ``command_executor.py``.
    Returns ``HookResult(action="continue")`` on any parsing error.
    """
    stripped = body.strip()
    if not stripped:
        return HookResult(action="continue")

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("http hook '%s' returned non-JSON body: %.200s", hook_name, stripped)
        return HookResult(action="continue")

    if not isinstance(data, dict):
        logger.warning("http hook '%s' response body is not a JSON object", hook_name)
        return HookResult(action="continue")

    # permissionDecision takes precedence — metadata is not yet populated at
    # this point, so pass an empty dict rather than a shared reference.
    if "permissionDecision" in data:
        decision = data["permissionDecision"]
        if decision in ("approve", "deny", "ask"):
            return HookResult(
                action="permission_decision",
                decision=decision,  # type: ignore[arg-type]
                reason=data.get("reason"),
                metadata={},
            )
        logger.warning("http hook '%s' returned unknown permissionDecision: %s", hook_name, decision)

    metadata: dict[str, object] = {}

    def _safe_additional_context(raw: object) -> object | None:
        """Cap additionalContext to 8 KiB (JSON-serialised) and sanitize strings."""
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = _sanitize_value(raw)
        try:
            serialized = json.dumps(raw)
        except (TypeError, ValueError):
            logger.warning("http hook '%s' additionalContext is not JSON-serialisable; discarding", hook_name)
            return None
        if len(serialized) > 8192:
            logger.warning(
                "http hook '%s' additionalContext exceeds 8 KiB (%d bytes); discarding",
                hook_name,
                len(serialized),
            )
            return None
        return raw

    # stopReason → block
    if "stopReason" in data:
        return HookResult(
            action="block",
            reason=str(data["stopReason"]),
            metadata=metadata,
        )

    # updatedInput → replace
    if "updatedInput" in data:
        if "additionalContext" in data:
            safe_ctx = _safe_additional_context(data["additionalContext"])
            if safe_ctx is not None:
                metadata["additionalContext"] = safe_ctx
        return HookResult(
            action="replace",
            value=data["updatedInput"],
            metadata=metadata,
        )

    # additionalContext only → continue with metadata
    if "additionalContext" in data:
        safe_ctx = _safe_additional_context(data["additionalContext"])
        if safe_ctx is not None:
            metadata["additionalContext"] = safe_ctx
        return HookResult(action="continue", metadata=metadata)

    # Explicit continue field (deprecated compat)
    if "continue" in data:
        cont = data["continue"]
        if cont is False or cont == "block":
            reason = data.get("reason")
            logger.warning(
                "http hook '%s' used deprecated 'continue: false' to block; "
                "prefer 'stopReason' for explicit blocking behaviour",
                hook_name,
            )
            return HookResult(action="block", reason=reason)

    return HookResult(action="continue", metadata=metadata)


def _fail_result(manifest: HookManifest, reason: str) -> HookResult:
    """Return continue or block based on ``manifest.fail_open``."""
    if manifest.fail_open:
        return HookResult(action="continue")
    return HookResult(action="block", reason=reason)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class HttpHookExecutor:
    """Executes hooks by making an HTTP request to ``manifest.url``.

    Implements the ``HookExecutor`` protocol.

    The HTTP client (``httpx.AsyncClient``) is created fresh per call so that
    per-manifest TLS and timeout settings are always honoured.  The overhead is
    negligible relative to the network round-trip.
    """

    async def execute(self, context: HookContext, manifest: HookManifest) -> HookResult:
        assert manifest.url is not None, "HttpHookExecutor requires manifest.url"

        payload: dict[str, Any] = _build_sanitized_hook_input(context, manifest)
        timeout_s: float = manifest.timeout_ms / 1000.0
        verify: bool = _tls_verify()

        # Build headers: start with mandatory Content-Type, then merge operator
        # headers (operator headers win in case of collision).
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if manifest.http_headers:
            headers.update(manifest.http_headers)

        try:
            async with httpx.AsyncClient(verify=verify, timeout=httpx.Timeout(timeout_s), follow_redirects=False) as client:
                response = await client.request(
                    method=manifest.http_method,
                    url=manifest.url,
                    content=json.dumps(payload, ensure_ascii=False).encode(),
                    headers=headers,
                )
        except httpx.TimeoutException:
            logger.warning(
                "http hook '%s' timed out after %.1fs (url=%s)",
                manifest.name,
                timeout_s,
                manifest.url,
            )
            return _fail_result(
                manifest,
                f"Hook '{manifest.name}' timed out after {manifest.timeout_ms}ms",
            )
        except Exception:
            logger.exception(
                "http hook '%s' raised an unexpected exception (url=%s)",
                manifest.name,
                manifest.url,
            )
            return _fail_result(
                manifest,
                f"Hook '{manifest.name}' encountered an unexpected error",
            )

        status = response.status_code

        # 204 No Content — explicit no-op, always continue
        if status == 204:
            return HookResult(action="continue")

        # 200 OK — parse response body as HookResult
        if status == 200:
            return _parse_http_response_body(response.text, manifest.name)

        # 4xx — client error (bad request, auth failure, not found, etc.)
        if 400 <= status < 500:
            logger.warning(
                "http hook '%s' received 4xx status %d from %s",
                manifest.name,
                status,
                manifest.url,
            )
            return _fail_result(
                manifest,
                f"Hook '{manifest.name}' received HTTP {status}",
            )

        # 5xx — server error
        if 500 <= status < 600:
            logger.error(
                "http hook '%s' received 5xx status %d from %s",
                manifest.name,
                status,
                manifest.url,
            )
            return _fail_result(
                manifest,
                f"Hook '{manifest.name}' received HTTP {status}",
            )

        # Unexpected status code (1xx, 3xx, etc.) — treat as error
        logger.warning(
            "http hook '%s' received unexpected status %d from %s",
            manifest.name,
            status,
            manifest.url,
        )
        return _fail_result(
            manifest,
            f"Hook '{manifest.name}' received unexpected HTTP {status}",
        )


# ---------------------------------------------------------------------------
# Self-register into the executor registry
# ---------------------------------------------------------------------------

_REGISTRY["http"] = HttpHookExecutor()
