"""Tests for the managed-inference turn-boundary credit pre-check.

Hermetic: the HTTP GET is injected, so no network/api-proxy is needed.
"""
from __future__ import annotations

import asyncio

from magi_agent.runtime.managed_credit_precheck import (
    ManagedPrecheckConfig,
    check_managed_credit_balance,
    resolve_managed_precheck_config,
)


def _fake_get(status: int, payload: dict):
    async def _get(url: str, headers: dict, timeout_s: float):  # noqa: ARG001
        return status, payload

    return _get


# ── resolve_managed_precheck_config ─────────────────────────────────────────


def test_config_none_when_flag_off():
    env = {"MAGI_LLM_API_BASE": "https://p", "MAGI_LLM_API_KEY": "gw_1"}
    assert resolve_managed_precheck_config(env) is None


def test_config_none_when_proxy_env_missing():
    env = {"MAGI_MANAGED_INFERENCE_ENABLED": "1"}
    assert resolve_managed_precheck_config(env) is None


def test_config_resolves_when_enabled_and_proxy_set():
    env = {
        "MAGI_MANAGED_INFERENCE_ENABLED": "1",
        "MAGI_LLM_API_BASE": "https://proxy.clawy.pro",
        "MAGI_LLM_API_KEY": "gw_abc",
    }
    cfg = resolve_managed_precheck_config(env)
    assert cfg == ManagedPrecheckConfig(
        api_proxy_url="https://proxy.clawy.pro", gateway_token="gw_abc"
    )


# ── check_managed_credit_balance ────────────────────────────────────────────

_CFG = ManagedPrecheckConfig(api_proxy_url="https://p", gateway_token="gw_1")


def _run(coro):
    return asyncio.run(coro)


def test_ok_when_balance_covers_floor():
    d = _run(
        check_managed_credit_balance(
            config=_CFG,
            http_get=_fake_get(200, {"balanceCents": 500, "grantedCents": 2900}),
        )
    )
    assert d.ok is True
    assert d.reason == "ok"
    assert d.block is False
    assert d.balance_cents == 500
    assert d.low_balance is False


def test_blocks_on_zero_balance():
    d = _run(
        check_managed_credit_balance(
            config=_CFG,
            http_get=_fake_get(200, {"balanceCents": 0, "grantedCents": 2900}),
        )
    )
    assert d.ok is False
    assert d.block is True
    assert d.reason == "insufficient_credits"


def test_low_balance_flag_at_15_percent():
    # 15% of 2900 = 435; a balance at/below that is low but still OK.
    d = _run(
        check_managed_credit_balance(
            config=_CFG,
            http_get=_fake_get(200, {"balanceCents": 400, "grantedCents": 2900}),
        )
    )
    assert d.ok is True
    assert d.low_balance is True


def test_fails_open_on_non_200():
    d = _run(
        check_managed_credit_balance(
            config=_CFG,
            http_get=_fake_get(500, {}),
        )
    )
    assert d.ok is True
    assert d.reason == "error"


def test_fails_open_on_http_exception():
    async def _raise(url, headers, timeout_s):  # noqa: ARG001
        raise RuntimeError("network down")

    d = _run(check_managed_credit_balance(config=_CFG, http_get=_raise))
    assert d.ok is True
    assert d.reason == "error"


def test_fails_open_on_garbage_balance_field():
    # A 200 with a non-numeric balance is a contract violation, NOT a genuine
    # zero — fail open rather than block a paying user on a bad response.
    d = _run(
        check_managed_credit_balance(
            config=_CFG,
            http_get=_fake_get(200, {"balanceCents": "not-a-number"}),
        )
    )
    assert d.ok is True
    assert d.reason == "error"


def test_fails_open_on_missing_balance_field():
    d = _run(check_managed_credit_balance(config=_CFG, http_get=_fake_get(200, {})))
    assert d.ok is True
    assert d.reason == "error"
