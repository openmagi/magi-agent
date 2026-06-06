"""Default-off provider router with retry and fallback for live web acquisition.

PR-A: Adds ``WebAcquisitionProviderRouter`` that wraps ``LiveWebAcquisitionProviderPack``
with an ordered provider list, per-provider retry (exponential backoff), and graceful
degradation when all providers are exhausted.

Key invariants preserved:
- The router NEVER calls a provider directly; it always delegates through
  ``LiveWebAcquisitionProviderPack.run()`` so the SSRF firewall in
  ``_validate_live_request`` always fires first.
- The router NEVER reads or modifies ``authority_flags``; those are sealed
  ``Literal[False]`` values owned by ``WebAcquisitionProviderAuthorityFlags``.
- ``ProviderRouterConfig(enabled=False)`` by default — zero behaviour change
  for any agent or test that does not opt in.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.web_acquisition.live_provider_pack import (
    LiveWebAcquisitionProviderPack,
    WebAcquisitionProviderOperation,
    WebAcquisitionProviderRequest,
    WebAcquisitionProviderResult,
    WebAcquisitionProviderStatus,
)
from magi_agent.runtime.provider_receipts import provider_digest
from magi_agent.web_acquisition.policy import safe_metadata


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

# Status values that trigger a move to the next provider in the fallback chain.
# "no_answer" is intentionally excluded: the provider returned content but
# nothing matched the query — that is a valid (if empty) search result, not
# an infrastructure failure that warrants trying another provider.
_DEFAULT_FALLBACK_ON_STATUS: tuple[str, ...] = (
    "repair_required",
    "provider_execution_failed",
    "provider_timeout",
)

# Reason codes within a repair_required result that indicate a transient
# condition (eligible for same-provider retry before falling back).
_TRANSIENT_REASON_CODES: frozenset[str] = frozenset(
    {
        "provider_execution_failed",
        "provider_timeout",
    }
)


class ProviderRouterConfig(BaseModel):
    """Configuration for the provider router.  Default-OFF.

    All fields are frozen and validated; ``model_construct`` is blocked to
    prevent bypassing validation.
    """

    model_config = _MODEL_CONFIG

    enabled: bool = False
    providers: tuple[str, ...] = ()
    max_attempts_per_provider: int = Field(default=1, ge=1, le=3)
    base_retry_delay_ms: int = Field(default=200, ge=0)
    max_retry_delay_ms: int = Field(default=2_000, ge=0)
    fallback_on_status: tuple[str, ...] = Field(
        default=_DEFAULT_FALLBACK_ON_STATUS,
    )


def _backoff_ms(attempt: int, config: ProviderRouterConfig) -> float:
    """Return the sleep duration in ms for a retry *attempt* (1-based).

    ``attempt=1`` means this is the *first* retry of the same provider (after
    the initial failure).  ``attempt=0`` would be the initial call — not
    eligible for backoff.

    The schedule is ``base * 2^(attempt-1)`` capped at ``max``, plus ±20%
    jitter applied symmetrically around the calculated value.
    """
    if attempt <= 0:
        return 0.0
    raw = config.base_retry_delay_ms * (2 ** (attempt - 1))
    capped = min(float(raw), float(config.max_retry_delay_ms))
    jitter_range = capped * 0.20
    return capped + random.uniform(-jitter_range, jitter_range)


class WebAcquisitionProviderRouter:
    """Routes a ``WebAcquisitionProviderRequest`` through an ordered provider list.

    Each provider in ``providers`` must be an object with
    ``openmagi_live_provider = True``.  The router calls
    ``LiveWebAcquisitionProviderPack.run()`` for every attempt; the pack owns
    the SSRF firewall and ``authority_flags`` sealing.
    """

    def __init__(
        self,
        *,
        pack: LiveWebAcquisitionProviderPack,
        config: ProviderRouterConfig,
        providers: Mapping[str, object],
    ) -> None:
        self._pack = pack
        self.config = config
        self._providers = dict(providers)

    def run(
        self,
        request: WebAcquisitionProviderRequest,
        *,
        _sleep: bool = True,
    ) -> WebAcquisitionProviderResult:
        """Run the request through the provider chain with retry + fallback.

        Parameters
        ----------
        request:
            The provider request to execute.
        _sleep:
            Internal hook.  Set to ``False`` in tests to skip ``time.sleep``
            without monkeypatching.
        """
        if not self.config.enabled:
            return _disabled_result(request)

        provider_names = list(self.config.providers)
        if not provider_names:
            return _exhausted_result(request, reason_codes=("router_no_providers_configured",))

        last_result: WebAcquisitionProviderResult | None = None
        all_reason_codes: list[str] = []

        for name in provider_names:
            provider = self._providers.get(name)
            if provider is None:
                all_reason_codes.append(f"provider_missing:{name}")
                continue

            # Per-provider attempt loop (initial call + up to max_attempts_per_provider-1 retries).
            for attempt in range(self.config.max_attempts_per_provider):
                if attempt > 0:
                    # This is a retry of the same provider — apply backoff.
                    delay_ms = _backoff_ms(attempt, self.config)
                    if _sleep and delay_ms > 0:
                        time.sleep(delay_ms / 1000.0)

                # Build a provider-specific request: swap providerName so the
                # pack's allowlist check matches the current provider's key.
                scoped_request = _with_provider_name(request, name)
                result = self._pack.run(scoped_request, provider=provider)
                last_result = result

                if result.status == "ok" or result.status == "no_answer":
                    # "ok" → return immediately.
                    # "no_answer" → provider returned content but nothing matched;
                    # treat as a valid (if empty) result — do NOT fall back.
                    return result

                # Check if this status triggers a fallback (vs. staying on same provider).
                if result.status not in self.config.fallback_on_status:
                    # Blocked / disabled / approval_required → not retriable, move to next.
                    reason = result.reason_codes[0] if result.reason_codes else result.status
                    all_reason_codes.append(f"provider_skipped:{name}:{reason}")
                    break

                # Transient failure: if we have retries left on this provider, loop again.
                reason_code = result.reason_codes[0] if result.reason_codes else ""
                is_transient = reason_code in _TRANSIENT_REASON_CODES
                has_retry = attempt + 1 < self.config.max_attempts_per_provider

                if is_transient and has_retry:
                    # Continue the inner loop (will sleep + retry same provider).
                    all_reason_codes.append(f"provider_retry:{name}:{reason_code}:{attempt + 1}")
                    continue

                # Exhausted retries for this provider → record and fall back.
                reason = result.reason_codes[0] if result.reason_codes else result.status
                all_reason_codes.append(f"provider_failed:{name}:{reason}")
                break

        # All providers exhausted.
        return _exhausted_result(
            request,
            reason_codes=("all_providers_exhausted", *all_reason_codes),
            last_result=last_result,
        )


def build_provider_router(
    config: ProviderRouterConfig,
    pack: LiveWebAcquisitionProviderPack,
    providers: Mapping[str, object],
) -> "WebAcquisitionProviderRouter | None":
    """Factory that returns a router only when the config is enabled.

    Returns ``None`` when ``config.enabled`` is ``False`` so callers can safely
    check ``if router is not None`` before routing.
    """
    if not config.enabled:
        return None
    return WebAcquisitionProviderRouter(pack=pack, config=config, providers=providers)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _with_provider_name(
    request: WebAcquisitionProviderRequest,
    provider_name: str,
) -> WebAcquisitionProviderRequest:
    """Return a shallow copy of *request* with ``provider_name`` replaced.

    Needed so the pack's provider-allowlist check matches the current provider's
    key without mutating the immutable Pydantic model.
    """
    data = request.model_dump(by_alias=True, mode="python", warnings=False)
    data["providerName"] = provider_name
    return WebAcquisitionProviderRequest.model_validate(data)


def _disabled_result(request: WebAcquisitionProviderRequest) -> WebAcquisitionProviderResult:
    from magi_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderAuthorityFlags,
    )

    digest = provider_digest(_request_payload(request))
    return WebAcquisitionProviderResult(
        status="disabled",
        operation=request.operation,
        requestDigest=digest,
        sourceRecords=(),
        reasonCodes=("provider_router_disabled",),
        diagnosticMetadata=safe_metadata({}),
        authorityFlags=WebAcquisitionProviderAuthorityFlags(),
    )


def _exhausted_result(
    request: WebAcquisitionProviderRequest,
    *,
    reason_codes: tuple[str, ...],
    last_result: WebAcquisitionProviderResult | None = None,
) -> WebAcquisitionProviderResult:
    from magi_agent.web_acquisition.live_provider_pack import (
        WebAcquisitionProviderAuthorityFlags,
    )

    digest = provider_digest(_request_payload(request))
    diag: dict[str, object] = {"exhausted": True}
    if last_result is not None:
        diag["lastStatus"] = last_result.status
        diag["lastReasonCodes"] = list(last_result.reason_codes)
    return WebAcquisitionProviderResult(
        status="repair_required",
        operation=request.operation,
        requestDigest=digest,
        sourceRecords=(),
        reasonCodes=reason_codes,
        diagnosticMetadata=safe_metadata(diag),
        authorityFlags=WebAcquisitionProviderAuthorityFlags(),
    )


def _request_payload(request: WebAcquisitionProviderRequest) -> dict[str, object]:
    return {
        "operation": request.operation,
        "requestId": request.request_id,
        "providerName": request.provider_name,
        "query": request.query,
        "url": request.url,
        "approvalGranted": request.approval_granted,
    }


__all__ = [
    "ProviderRouterConfig",
    "WebAcquisitionProviderRouter",
    "build_provider_router",
]
