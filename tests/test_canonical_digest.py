"""Tests for the single canonical content-addressing kernel (C-5).

``canonical_digest`` lives in ``magi_agent.ops.safety`` and is the single
canonical-JSON -> sha256 primitive. ``FrozenContractModel`` lives in
``magi_agent.ops.authority`` (re-exported from ``ops.safety``) and is the single
frozen-contract base (config trio + disabled escape hatches).

PARITY: ``canonical_digest`` must produce byte-identical digests to the
de-facto standard ``connectors/registry._digest_payload`` (the most-copied form:
``sort_keys=True, separators=(",",":"), default=str, allow_nan=False`` -> sha256,
defaulting ``ensure_ascii=True``). The only intended behavioral change is that
copies which omitted ``allow_nan=False`` now correctly raise on NaN/Inf.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from magi_agent.ops.safety import FrozenContractModel, canonical_digest


def _reference_digest(payload: dict[str, object]) -> str:
    """The de-facto standard form copied across ~11 ``_digest_payload`` helpers."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_canonical_digest_returns_sha256_ref() -> None:
    digest = canonical_digest({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_canonical_digest_is_stable_across_key_order() -> None:
    assert canonical_digest({"a": 1, "b": 2}) == canonical_digest({"b": 2, "a": 1})


def test_canonical_digest_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_digest({"x": float("nan")})


def test_canonical_digest_rejects_inf() -> None:
    with pytest.raises(ValueError):
        canonical_digest({"x": float("inf")})


def test_canonical_digest_uses_default_str_for_unserializable() -> None:
    from datetime import UTC, datetime

    when = datetime(2026, 6, 18, tzinfo=UTC)
    # default=str coerces non-JSON-native objects rather than raising.
    assert canonical_digest({"at": when}) == _reference_digest({"at": when})


@pytest.mark.parametrize(
    "payload",
    [
        {"a": 1, "b": "two", "c": [1, 2, 3]},
        {"nested": {"z": 1, "a": 2}, "flag": True, "n": None},
        {"unicode": "한국어 텍스트 ✅", "emoji": "🚀"},
        {"int": 10, "neg": -5, "float": 1.5},
        {},
    ],
)
def test_canonical_digest_byte_compat_with_reference(payload: dict[str, object]) -> None:
    """The kernel must equal the de-facto standard for representative payloads,
    INCLUDING non-ASCII payloads (ensure_ascii defaults to True, matching the
    shipped copies) so durable digests do not change."""
    assert canonical_digest(payload) == _reference_digest(payload)


def test_canonical_digest_byte_compat_with_connectors_registry() -> None:
    from magi_agent.connectors.registry import _digest_payload

    payload = {"recordKind": "demo", "value": "한글", "n": 7}
    assert canonical_digest(payload) == _digest_payload(payload)


# ---- FrozenContractModel ----------------------------------------------------


def test_frozen_contract_model_is_frozen_and_forbids_extra() -> None:
    class Demo(FrozenContractModel):
        value: int

    instance = Demo(value=1)
    with pytest.raises(Exception):
        instance.value = 2  # frozen
    with pytest.raises(Exception):
        Demo(value=1, surprise=2)  # extra="forbid"


def test_frozen_contract_model_disables_model_construct() -> None:
    class Demo(FrozenContractModel):
        value: int = 0

    with pytest.raises(ValueError):
        Demo.model_construct(value=5)


def test_frozen_contract_model_disables_model_copy_update() -> None:
    class Demo(FrozenContractModel):
        value: int = 0

    instance = Demo(value=1)
    with pytest.raises(ValueError):
        instance.model_copy(update={"value": 2})
    # copy without update is allowed and round-trips
    assert instance.model_copy().value == 1


def test_frozen_contract_model_populate_by_name() -> None:
    from pydantic import Field

    class Demo(FrozenContractModel):
        value: int = Field(alias="theValue")

    assert Demo(theValue=3).value == 3
    assert Demo(value=4).value == 4  # populate_by_name=True
