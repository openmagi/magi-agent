# tests/benchmarks/taubench/test_reliability.py
from __future__ import annotations

from magi_agent.benchmarks.taubench.reliability import ReliabilityConfig, validate_args

AIRLINE_SPEC = {
    "type": "object",
    "properties": {
        "flight_type": {"type": "string", "enum": ["one_way", "round_trip"]},
        "passengers": {"type": "integer"},
        "user_id": {"type": "string"},
    },
    "required": ["user_id"],
}


def test_config_defaults_all_off() -> None:
    c = ReliabilityConfig()
    assert (c.arg_validation, c.dup_write_guard, c.verify_before_final) == (False, False, False)
    assert c.any_enabled is False


def test_config_any_enabled() -> None:
    assert ReliabilityConfig(arg_validation=True).any_enabled is True


def test_validate_args_rejects_enum_mismatch() -> None:
    msg = validate_args(AIRLINE_SPEC, {"user_id": "u1", "flight_type": "one way"})
    assert msg is not None and "flight_type" in msg


def test_validate_args_rejects_missing_required() -> None:
    msg = validate_args(AIRLINE_SPEC, {"flight_type": "one_way"})
    assert msg is not None and "user_id" in msg


def test_validate_args_rejects_wrong_type() -> None:
    msg = validate_args(AIRLINE_SPEC, {"user_id": "u1", "passengers": "two"})
    assert msg is not None and "passengers" in msg


def test_validate_args_rejects_bool_for_integer() -> None:
    msg = validate_args(AIRLINE_SPEC, {"user_id": "u1", "passengers": True})
    assert msg is not None and "passengers" in msg


def test_validate_args_accepts_valid() -> None:
    assert validate_args(
        AIRLINE_SPEC, {"user_id": "u1", "flight_type": "one_way", "passengers": 2}
    ) is None


def test_validate_args_accepts_unknown_optional_key() -> None:
    assert validate_args(AIRLINE_SPEC, {"user_id": "u1", "note": "anything"}) is None


def test_validate_args_no_properties_passes() -> None:
    assert validate_args({"type": "object"}, {"x": 1}) is None
