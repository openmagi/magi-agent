"""TDD: MAGI_SERVE_EVIDENCE_ENABLED flag — strict default-OFF boolean.

Mirrors the pattern used for MAGI_SESSION_TRANSCRIPT_ENABLED and
MAGI_OBSERVABILITY_ENABLED: a single-line `_b(...)` entry in FLAGS, readable
via `flag_bool`, default=False, truthy values opt-in.

All tests are hermetic (no network, no model traffic, no disk I/O).
"""
from __future__ import annotations

import pytest

from magi_agent.config.flags import FLAGS, flag_bool

_FLAG = "MAGI_SERVE_EVIDENCE_ENABLED"


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
# Default-OFF behaviour
# ---------------------------------------------------------------------------


def test_flag_is_false_with_no_env() -> None:
    assert flag_bool(_FLAG, env={}) is False


def test_flag_is_false_when_unset_in_os_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    assert flag_bool(_FLAG) is False


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
