"""E-14 — shadow runner-input token estimate switches from UTF-8 byte
length to a real character/BPE estimate behind a soak flag.

Pre-E-14 ``shadow/gate5b4c3_runner_input_adapter._estimate_tokens``
returned ``len(value.encode("utf-8"))`` — byte length, not tokens.
Compared against ``request.budgets.max_estimated_input_tokens`` and
``max_total_estimated_tokens``, this over-counted ASCII ~4× and CJK ~3×
(1 Korean char ≈ 3 UTF-8 bytes but ≈ 1 token), so the serve path
spuriously dropped CJK turns as ``input_token_budget_exceeded``.

This module locks the new contract behind a default-OFF soak gate:

* Flag ``MAGI_SERVE_TOKEN_ESTIMATE_REAL`` OFF (today's default):
  byte-identical to the legacy heuristic — every existing budget-drop
  test keeps the same behavior.
* Flag ON: real estimate via ``shared/token_estimation.count_text_tokens``
  (tiktoken when available, otherwise ``len // 4``). CJK turns under
  the real-token budget are NO LONGER dropped. The byte cap
  (``max_sanitized_input_bytes``) is independent and still fires on
  oversized payloads — the real DoS guard is unchanged.

Per AGENTS.md flag-promotion-verification, the flip to default-ON is a
follow-up PR after canary soak under hosted env shape.
"""

from __future__ import annotations

import pytest

from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    _estimate_tokens,
    build_gate5b4c3_runner_input,
)


# ---------------------------------------------------------------------------
# Flag-OFF (default): byte-identical to today
# ---------------------------------------------------------------------------


def test_estimate_tokens_flag_off_returns_utf8_byte_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", raising=False)
    # ASCII: 5 bytes = 5 chars
    assert _estimate_tokens("hello") == 5
    # Korean: each char is 3 UTF-8 bytes
    assert _estimate_tokens("안녕하세요") == 15  # 5 chars × 3 bytes
    # Empty string short-circuits before the encode.
    assert _estimate_tokens("") == 0


def test_estimate_tokens_flag_explicitly_off_returns_byte_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "0")
    assert _estimate_tokens("안녕하세요") == 15


# ---------------------------------------------------------------------------
# Flag-ON: real character/BPE estimate (CJK no longer 3× over-counted)
# ---------------------------------------------------------------------------


def test_estimate_tokens_flag_on_returns_real_token_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend-agnostic check: the flag-ON path returns a count that is
    not the legacy UTF-8 byte length. (tiktoken-installed paths
    aggressively merge BPE on repeated chars; no-tiktoken paths use
    ``len // 4``. Both produce values strictly < the byte length.)"""

    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "1")
    out_ascii = _estimate_tokens("a" * 1000)
    assert out_ascii < 1000, out_ascii  # less than UTF-8 byte length
    out_korean = _estimate_tokens("안" * 1000)
    # 1000 KR chars = 3000 UTF-8 bytes; real estimate is strictly less.
    assert out_korean < 3000, out_korean


def test_estimate_tokens_flag_on_delegates_to_count_text_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic seam check: flag-ON routes through
    ``shared.token_estimation.count_text_tokens`` so the function is
    swappable for testing / tiktoken-variance isolation."""

    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "1")
    import magi_agent.shared.token_estimation as te

    seen: list[str] = []

    def _stub(text: str) -> int:
        seen.append(text)
        return 42

    monkeypatch.setattr(te, "count_text_tokens", _stub, raising=False)
    assert _estimate_tokens("hello") == 42
    assert seen == ["hello"]


def test_estimate_tokens_flag_on_empty_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "1")
    assert _estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# Integration via build_gate5b4c3_runner_input — verifies the budget gate
# behavior end-to-end with the new estimator path.
# ---------------------------------------------------------------------------


def _build_request_with_korean_input(char_count: int) -> object:
    """Synthesize a request payload with a Korean ``sanitizedCurrentTurnText``
    sized below the default 8192 byte cap (1 KR char = 3 bytes) but with
    enough real tokens to exercise the budget gate."""

    from tests.test_gate5b4c3_runner_input_adapter import _payload, _request

    base_payload = _payload()
    text = "안" * char_count  # 3 bytes per char, 1 token per char (real)
    return _request(
        turn={
            **base_payload["turn"],  # type: ignore[arg-type]
            "sanitizedCurrentTurnText": text,
        },
        # Defaults: maxSanitizedInputBytes=8192, maxEstimatedInputTokens=2048.
        # 1000 KR chars = 3000 bytes < 8192 (byte cap not tripped) and
        # 3000 byte-tokens > 2048 (old flag would drop) but ~250 real
        # tokens (new flag accepts).
    )


def test_korean_input_not_dropped_under_real_estimator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline E-14 win for Kevin's locale: a 1000-char Korean
    input is no longer dropped as ``input_token_budget_exceeded`` when
    the real-estimate flag is ON."""

    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "1")
    result = build_gate5b4c3_runner_input(
        _build_request_with_korean_input(1000)
    )
    assert result.status != "dropped" or result.reason not in (
        "input_token_budget_exceeded",
        "total_token_budget_exceeded",
    ), f"Expected accepted, got dropped/{result.reason}"


def test_korean_input_dropped_under_byte_heuristic_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression baseline: under the byte heuristic (today's default),
    a 1000-char Korean input is dropped as ``input_token_budget_exceeded``
    — 3000 bytes > 2048 token budget. This locks the false-drop the
    flag-ON path closes."""

    monkeypatch.delenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", raising=False)
    result = build_gate5b4c3_runner_input(
        _build_request_with_korean_input(1000)
    )
    assert result.status == "dropped"
    assert result.reason == "input_token_budget_exceeded"


def test_byte_cap_contract_validator_still_rejects_oversized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The byte cap is enforced at the contract level (pydantic
    ``model_validator``) BEFORE ``build_gate5b4c3_runner_input`` runs —
    that is the real DoS guard, untouched by E-14. The adapter-level
    byte check (``if input_bytes > ...max_sanitized_input_bytes``) is
    belt-and-suspenders; it can only fire if a caller bypasses the
    contract validator. Confirm the contract rejection is unchanged
    when the flag is ON."""

    from tests.test_gate5b4c3_runner_input_adapter import _payload, _request

    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "1")
    base_payload = _payload()
    huge_ascii = "x" * 16_384  # exceeds default 8192 byte cap
    with pytest.raises(Exception) as excinfo:
        _request(
            turn={
                **base_payload["turn"],  # type: ignore[arg-type]
                "sanitizedCurrentTurnText": huge_ascii,
            },
        )
    assert "exceeds configured" in str(excinfo.value) or "budget" in str(
        excinfo.value
    )


def test_genuinely_oversized_real_input_still_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even under the real estimator, an input whose real token count
    exceeds the budget IS dropped — the gate is not toothless, just
    correctly calibrated. Use varied content (BPE-resistant) so the
    test holds whether tiktoken is installed or not."""

    from tests.test_gate5b4c3_runner_input_adapter import _payload, _request

    monkeypatch.setenv("MAGI_SERVE_TOKEN_ESTIMATE_REAL", "1")
    base_payload = _payload()
    # Natural-text English doesn't compress under BPE: ~1 token per 4-5
    # chars. 10_000 chars ⇒ ~2200 tokens (tiktoken) or 2500 (no-tiktoken),
    # both well above the 1000-token budget.
    sentence = "The quick brown fox jumps over the lazy dog. "
    real_oversized = (sentence * (10_000 // len(sentence) + 1))[:10_000]
    result = build_gate5b4c3_runner_input(
        _request(
            turn={
                **base_payload["turn"],  # type: ignore[arg-type]
                "sanitizedCurrentTurnText": real_oversized,
            },
            budgets={
                "maxSanitizedInputBytes": 65536,  # generous byte cap
                "maxEstimatedInputTokens": 1000,  # tight token cap
            },
        )
    )
    assert result.status == "dropped"
    assert result.reason == "input_token_budget_exceeded"
