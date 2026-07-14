"""Turn-boundary credit pre-check for the managed-inference desktop tier.

When the OSS desktop app routes inference through Magi's api-proxy with a
managed-inference subscription token, this module lets the runtime hard-stop a
turn at the BOUNDARY (before any model/tool cost) when the subscriber's balance
can't cover it — instead of letting the api-proxy reject mid-stream with a 402,
which would leave a half-applied agent turn.

Design (clawy docs/plans/2026-07-14-oss-magi-managed-inference-subscription-design.md):

* Only active for managed inference: gated on ``MAGI_MANAGED_INFERENCE_ENABLED``
  plus the proxy-routing env (``MAGI_LLM_API_BASE`` + ``MAGI_LLM_API_KEY``) the
  desktop app injects. For every other caller (hosted bots, BYO key) this is
  inert — ``resolve_managed_precheck_config`` returns ``None``.
* The api-proxy remains the source of truth for billing (it reserves credits per
  request). This pre-check is a UX gate, so it FAILS OPEN: any network/parse
  error lets the turn proceed and the api-proxy enforces the real limit.
* Hard-stop only on a definitive ``balance <= floor``. A low-balance flag (default
  15% of the monthly grant) lets the client warn before the wall.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Optional

# (url, json_body_or_none) -> (status_code, parsed_json_dict)
CreditHttpGet = Callable[[str, float], Awaitable[tuple[int, dict]]]

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULT_LOW_BALANCE_RATIO = 0.15
DEFAULT_MIN_BALANCE_CENTS = 1
DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ManagedPrecheckConfig:
    """Resolved managed-inference routing target for the balance query."""

    api_proxy_url: str
    gateway_token: str


@dataclass(frozen=True)
class CreditPrecheckDecision:
    """Outcome of a turn-boundary credit pre-check.

    ``ok`` True means the turn may proceed. It is True both when the balance
    covers the floor AND when the check could not run / errored (fail-open).
    ``block`` is the inverse and is only ever True on a definitive insufficient
    balance.
    """

    ok: bool
    reason: str  # "ok" | "insufficient_credits" | "skipped" | "error"
    balance_cents: int = 0
    granted_cents: int = 0
    low_balance: bool = False

    @property
    def block(self) -> bool:
        return not self.ok


def resolve_managed_precheck_config(
    env: Mapping[str, str],
) -> Optional[ManagedPrecheckConfig]:
    """Return the balance-query target when managed inference is active, else None.

    Active means the desktop app has enabled managed inference and injected the
    proxy-routing env. Any missing piece → None (pre-check inert).
    """
    if str(env.get("MAGI_MANAGED_INFERENCE_ENABLED", "")).strip().lower() not in _TRUTHY:
        return None
    api_base = (env.get("MAGI_LLM_API_BASE") or "").strip()
    token = (env.get("MAGI_LLM_API_KEY") or "").strip()
    if not api_base or not token:
        return None
    return ManagedPrecheckConfig(api_proxy_url=api_base, gateway_token=token)


async def _httpx_get(url: str, headers: dict, timeout_s: float) -> tuple[int, dict]:
    import httpx  # noqa: PLC0415 — optional dep, imported lazily

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.get(url, headers=headers)
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = {}
        return response.status_code, payload if isinstance(payload, dict) else {}


async def check_managed_credit_balance(
    *,
    config: ManagedPrecheckConfig,
    min_balance_cents: int = DEFAULT_MIN_BALANCE_CENTS,
    low_balance_ratio: float = DEFAULT_LOW_BALANCE_RATIO,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    http_get: Optional[Callable[[str, dict, float], Awaitable[tuple[int, dict]]]] = None,
) -> CreditPrecheckDecision:
    """Query the api-proxy balance endpoint and decide whether the turn may start.

    FAILS OPEN: a non-200 status, a network error, or an unparseable body all
    return ``ok=True`` (reason ``"error"``) — the api-proxy is the real gate. A
    hard block (``ok=False``, reason ``"insufficient_credits"``) is returned only
    when the endpoint reports a balance at or below the floor.
    """
    url = config.api_proxy_url.rstrip("/") + "/v1/credits/balance"
    headers = {
        "authorization": f"Bearer {config.gateway_token}",
        "x-api-key": config.gateway_token,
    }
    get = http_get or _httpx_get
    try:
        status, payload = await get(url, headers, timeout_s)
    except Exception:  # noqa: BLE001 — never wedge a turn on a network blip
        return CreditPrecheckDecision(ok=True, reason="error")
    if not (200 <= status < 300):
        return CreditPrecheckDecision(ok=True, reason="error")

    # A 200 must carry a numeric balance. A missing/garbage field is a contract
    # violation, not a genuine zero — fail OPEN rather than block a paying user.
    balance_cents = _as_int_or_none(payload.get("balanceCents"))
    if balance_cents is None:
        return CreditPrecheckDecision(ok=True, reason="error")
    granted_cents = _as_int_or_none(payload.get("grantedCents")) or 0
    low_balance = (
        granted_cents > 0 and balance_cents <= int(granted_cents * low_balance_ratio)
    )

    if balance_cents < min_balance_cents:
        return CreditPrecheckDecision(
            ok=False,
            reason="insufficient_credits",
            balance_cents=balance_cents,
            granted_cents=granted_cents,
            low_balance=low_balance,
        )
    return CreditPrecheckDecision(
        ok=True,
        reason="ok",
        balance_cents=balance_cents,
        granted_cents=granted_cents,
        low_balance=low_balance,
    )


def _as_int_or_none(value: object) -> Optional[int]:
    """Coerce to int, or None when the value is absent/non-numeric (contract
    violation → caller fails open rather than treating it as a genuine zero)."""
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
