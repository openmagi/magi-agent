"""Exposure<->authority coupling for local ``magi serve`` (P0 security fix).

When the resolved serve bind host is non-loopback, the local serve owner must
NOT run at ``bypassPermissions`` (YOLO) unless the explicit
``MAGI_SERVE_REMOTE_YOLO`` opt-in is set. Loopback binds keep the current YOLO
baseline unchanged.
"""

from __future__ import annotations

import pytest

from magi_agent.transport.chat_shared import (
    _is_loopback_host,
    local_serve_permission_mode,
)


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "localhost", "127.0.0.5"],
)
def test_loopback_hosts_detected(host: str) -> None:
    assert _is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.10", "10.0.0.4", "::", "example.com"],
)
def test_non_loopback_hosts_detected(host: str) -> None:
    assert _is_loopback_host(host) is False


def test_loopback_unset_optin_keeps_bypass() -> None:
    env = {"MAGI_SERVE_HOST": "127.0.0.1"}
    assert local_serve_permission_mode(env=env) == "bypassPermissions"


def test_default_host_keeps_bypass() -> None:
    # MAGI_SERVE_HOST unset now defaults to loopback -> YOLO preserved.
    assert local_serve_permission_mode(env={}) == "bypassPermissions"


def test_non_loopback_unset_optin_downgrades_to_default() -> None:
    env = {"MAGI_SERVE_HOST": "0.0.0.0"}
    assert local_serve_permission_mode(env=env) == "default"


def test_non_loopback_with_optin_keeps_bypass() -> None:
    env = {"MAGI_SERVE_HOST": "0.0.0.0", "MAGI_SERVE_REMOTE_YOLO": "1"}
    assert local_serve_permission_mode(env=env) == "bypassPermissions"


def test_lan_ip_unset_optin_downgrades() -> None:
    env = {"MAGI_SERVE_HOST": "192.168.1.50"}
    assert local_serve_permission_mode(env=env) == "default"


def test_remote_yolo_flag_registered_default_off() -> None:
    from magi_agent.config.flags import flag_bool

    assert flag_bool("MAGI_SERVE_REMOTE_YOLO", env={}) is False
