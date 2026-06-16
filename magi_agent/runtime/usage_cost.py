"""Best-effort USD cost for a turn's token usage.

The engine already folds per-turn token usage into a snake_case mapping
(``input_tokens`` / ``output_tokens`` / ``cache_read_tokens``; see
``cli/engine.py:_adk_usage_metadata``). This module turns that mapping into a
dollar figure using litellm's maintained per-model price map, so callers never
hand-maintain a pricing table.

Hard rules:

* Never raise — an unknown model, missing usage, or an absent/old litellm all
  resolve to ``0.0`` rather than breaking the caller (a live chat turn).
* Never fabricate — ``0.0`` means "not priced", not "free". Cost is computed
  from real ``input``/``output`` token counts only; cache tokens are recorded
  elsewhere for display but are not priced here (litellm's cache pricing needs
  provider-specific signals this seam does not carry).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

__all__ = ["compute_cost_usd"]

# A pricing function with litellm's ``cost_per_token`` shape:
# ``(model, prompt_tokens, completion_tokens) -> (prompt_cost, completion_cost)``.
CostPerToken = Callable[..., tuple[float, float]]


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _litellm_cost_per_token() -> CostPerToken | None:
    try:
        from litellm import cost_per_token  # noqa: PLC0415 — lazy: heavy import
    except Exception:  # noqa: BLE001 — litellm absent (e.g. test env) → unpriced
        return None
    return cost_per_token


def compute_cost_usd(
    model: str | None,
    usage: Mapping[str, object] | None,
    *,
    cost_per_token: CostPerToken | None = None,
) -> float:
    """Return the USD cost for ``usage`` under ``model``, or ``0.0``.

    Args:
        model: The model identifier (bare like ``"claude-sonnet-4-5"`` or
            provider-prefixed like ``"anthropic/claude-..."``; litellm accepts
            both). ``None``/empty resolves to ``0.0``.
        usage: A token-usage mapping using the engine's snake_case keys
            (``input_tokens``, ``output_tokens``). ``None``/empty or all-zero
            token counts resolve to ``0.0``.
        cost_per_token: Optional pricing function (injected for tests). Defaults
            to litellm's ``cost_per_token``; when litellm is unavailable the
            result is ``0.0``.
    """
    if not model or not usage:
        return 0.0

    tokens_in = _non_negative_int(usage.get("input_tokens"))
    tokens_out = _non_negative_int(usage.get("output_tokens"))
    if tokens_in <= 0 and tokens_out <= 0:
        return 0.0

    pricer = cost_per_token if cost_per_token is not None else _litellm_cost_per_token()
    if pricer is None:
        return 0.0

    try:
        prompt_cost, completion_cost = pricer(
            model=model,
            prompt_tokens=tokens_in,
            completion_tokens=tokens_out,
        )
        total = float(prompt_cost) + float(completion_cost)
    except Exception:  # noqa: BLE001 — unknown model → unpriced
        return 0.0

    return total if total > 0 else 0.0
