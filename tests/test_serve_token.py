"""Tests for the per-install local serve gateway token (P0 security fix).

Replaces the publicly-known ``local-dev-token`` constant with a random,
per-install token persisted at ``~/.magi/serve_token`` (mode 0600) and reused
across runs. Local-mode detection keys on this resolved token, so hosted
deployments (explicit ``GATEWAY_TOKEN``) never trip the local-mode gates.
"""

from __future__ import annotations

import stat

import pytest

from magi_agent.config.serve_token import (
    LOCAL_DEV_TOKEN_SENTINEL,
    _serve_token_path,
    is_local_serve_token,
    local_serve_gateway_token,
)


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    # The resolver caches within the process; clear before/after each test so a
    # generated token from one test does not leak into the next.
    local_serve_gateway_token.cache_clear()
    yield
    local_serve_gateway_token.cache_clear()


def _redirect_magi_home(monkeypatch, tmp_path):
    # Point ~/.magi at a temp dir by overriding the home resolution the token
    # path uses. Both HOME (posix) and USERPROFILE (win) are set for safety.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("MAGI_CONFIG", raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE", raising=False)


def test_token_is_generated_and_not_the_public_constant(monkeypatch, tmp_path):
    _redirect_magi_home(monkeypatch, tmp_path)
    token = local_serve_gateway_token()
    assert token
    assert token != LOCAL_DEV_TOKEN_SENTINEL
    # token_urlsafe(32) yields a >=32-char urlsafe string.
    assert len(token) >= 32


def test_token_is_persisted_with_0600_mode(monkeypatch, tmp_path):
    _redirect_magi_home(monkeypatch, tmp_path)
    token = local_serve_gateway_token()
    path = _serve_token_path()
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip() == token
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_token_is_reused_across_runs(monkeypatch, tmp_path):
    _redirect_magi_home(monkeypatch, tmp_path)
    first = local_serve_gateway_token()
    # Simulate a fresh process: clear the in-process cache but keep the file.
    local_serve_gateway_token.cache_clear()
    second = local_serve_gateway_token()
    assert first == second


def test_is_local_serve_token_true_for_resolved_token(monkeypatch, tmp_path):
    _redirect_magi_home(monkeypatch, tmp_path)
    token = local_serve_gateway_token()
    assert is_local_serve_token(token) is True


def test_is_local_serve_token_false_for_public_constant(monkeypatch, tmp_path):
    _redirect_magi_home(monkeypatch, tmp_path)
    # The old publicly-known constant must NOT be accepted as a local token.
    assert is_local_serve_token(LOCAL_DEV_TOKEN_SENTINEL) is False


def test_is_local_serve_token_false_for_hosted_secret(monkeypatch, tmp_path):
    _redirect_magi_home(monkeypatch, tmp_path)
    assert is_local_serve_token("super-secret-hosted-token") is False
    assert is_local_serve_token(None) is False
