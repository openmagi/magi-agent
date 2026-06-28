"""TDD: MAGI_CHAT_AUDIT_PANEL_ENABLED flag — default-ON boolean.

A single-line ``_b(..., default=True)`` entry in FLAGS, readable via
``flag_bool``. The Audit panel is a read-only surfacing of existing
observability data (no new verdict production), so it ships ON; an operator can
hide it with an explicit ``=0``.

All tests are hermetic (no network, no model traffic, no disk I/O, no reliance
on ambient MAGI_* shell env — env is supplied explicitly).
"""
from __future__ import annotations

import pytest

from magi_agent.config.flags import FLAGS, flag_bool

_FLAG = "MAGI_CHAT_AUDIT_PANEL_ENABLED"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_flag_is_registered() -> None:
    names = {spec.name for spec in FLAGS}
    assert _FLAG in names, f"{_FLAG!r} not found in FLAGS registry"


def test_flag_kind_is_bool() -> None:
    spec = next(s for s in FLAGS if s.name == _FLAG)
    assert spec.kind == "bool", f"expected kind='bool', got {spec.kind!r}"


def test_flag_scope_is_public() -> None:
    spec = next(s for s in FLAGS if s.name == _FLAG)
    assert spec.scope == "public", f"expected scope='public', got {spec.scope!r}"


# ---------------------------------------------------------------------------
# Default-ON behaviour
# ---------------------------------------------------------------------------


def test_flag_is_true_with_no_env() -> None:
    assert flag_bool(_FLAG, env={}) is True


def test_flag_is_true_when_unset_in_os_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    assert flag_bool(_FLAG) is True


# ---------------------------------------------------------------------------
# Truthy opt-in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_flag_is_true_for_truthy_values(value: str) -> None:
    assert flag_bool(_FLAG, env={_FLAG: value}) is True


# ---------------------------------------------------------------------------
# Falsy (explicit off)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_flag_is_false_for_falsy_values(value: str) -> None:
    assert flag_bool(_FLAG, env={_FLAG: value}) is False
