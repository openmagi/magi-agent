# tests/benchmarks/taubench/test_reliability.py
from __future__ import annotations

from benchmarks.taubench.reliability import ReliabilityConfig, validate_args

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


from benchmarks.taubench.reliability import WriteLedger, looks_like_error


def test_is_write_by_prefix() -> None:
    led = WriteLedger()
    assert led.is_write("book_reservation") is True
    assert led.is_write("cancel_reservation") is True
    assert led.is_write("update_reservation_flights") is True
    assert led.is_write("send_certificate") is True
    assert led.is_write("get_reservation_details") is False


def test_repeat_write_same_name_and_args_order_independent() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"user_id": "u1", "flight": "F1"}, ok=True)
    assert led.is_repeat_write("book_reservation", {"flight": "F1", "user_id": "u1"}) is True


def test_not_repeat_when_args_differ() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"flight": "F1"}, ok=True)
    assert led.is_repeat_write("book_reservation", {"flight": "F2"}) is False


def test_not_repeat_when_prior_write_failed() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"flight": "F1"}, ok=False)
    assert led.is_repeat_write("book_reservation", {"flight": "F1"}) is False


def test_had_successful_write_and_last_errored_transitions() -> None:
    led = WriteLedger()
    assert led.had_successful_write() is False
    assert led.last_write_errored() is False  # empty ledger -> not errored
    led.record("book_reservation", {"x": 1}, ok=False)
    assert led.had_successful_write() is False
    assert led.last_write_errored() is True
    led.record("book_reservation", {"x": 1}, ok=True)
    assert led.had_successful_write() is True
    assert led.last_write_errored() is False


def test_looks_like_error() -> None:
    assert looks_like_error("Error: bad action") is True
    assert looks_like_error("  error - nope") is True
    assert looks_like_error("Reservation booked id=R1") is False
    assert looks_like_error(123) is False


from benchmarks.taubench.reliability import verify_final


def test_verify_final_nudges_on_success_claim_without_write() -> None:
    led = WriteLedger()
    msg = verify_final(led, "Your reservation is booked! Reservation ID HATHAT")
    assert msg is not None


def test_verify_final_nudges_when_last_write_errored() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"x": 1}, ok=False)
    assert verify_final(led, "All set — your booking is confirmed.") is not None


def test_verify_final_silent_when_success_backed_by_write() -> None:
    led = WriteLedger()
    led.record("book_reservation", {"x": 1}, ok=True)
    assert verify_final(led, "Your reservation is booked. Reservation ID R1.") is None


def test_verify_final_silent_without_success_language() -> None:
    led = WriteLedger()
    assert verify_final(led, "Can you confirm your travel dates first?") is None


from benchmarks.taubench.reliability import completion_review_nudge, is_conclusion


def test_config_completion_review_default_and_any_enabled() -> None:
    assert ReliabilityConfig().completion_review is False
    assert ReliabilityConfig(completion_review=True).any_enabled is True


def test_is_conclusion_detects_success_claim() -> None:
    assert is_conclusion("Your reservation is booked. Reservation ID R1.") is True


def test_is_conclusion_detects_refusal_and_closure() -> None:
    assert is_conclusion("I'm sorry, I'm unable to do that.") is True
    assert is_conclusion("Unfortunately I cannot cancel this.") is True
    assert is_conclusion("Is there anything else I can help with?") is True


def test_is_conclusion_silent_on_info_question() -> None:
    assert is_conclusion("Can you confirm your travel dates first?") is False


def test_is_conclusion_handles_empty() -> None:
    assert is_conclusion("") is False


def test_completion_review_nudge_is_general_no_domain_tokens() -> None:
    msg = completion_review_nudge()
    assert isinstance(msg, str) and len(msg) > 0
    low = msg.lower()
    for tok in ("flight", "reservation", "cabin", "airline", "baggage", "certificate"):
        assert tok not in low
