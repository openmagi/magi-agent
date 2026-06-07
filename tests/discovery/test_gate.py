from __future__ import annotations

import pytest

from magi_agent.discovery.gate import (
    GateDisabledError,
    ensure_discovery_enabled,
    is_discovery_enabled,
)


def test_disabled_by_default() -> None:
    assert is_discovery_enabled({}) is False
    with pytest.raises(GateDisabledError):
        ensure_discovery_enabled({})


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_values_enable(value: str) -> None:
    env = {"MAGI_DISCOVERY_ENABLED": value}
    assert is_discovery_enabled(env) is True
    ensure_discovery_enabled(env)  # does not raise


@pytest.mark.parametrize("value", ["0", "false", "", "no", "off"])
def test_falsy_values_stay_disabled(value: str) -> None:
    env = {"MAGI_DISCOVERY_ENABLED": value}
    assert is_discovery_enabled(env) is False
    with pytest.raises(GateDisabledError):
        ensure_discovery_enabled(env)
