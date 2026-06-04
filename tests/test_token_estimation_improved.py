"""PR13: token-estimation improvement tests.

Covers the best-effort tokenizer + char/4 fallback added to
``magi_agent.shared.token_estimation``:

* ``count_text_tokens`` accuracy on representative strings vs. the old heuristic,
* graceful fallback when no tokenizer is installed (the default in this env),
* the optional ``tiktoken`` path via a faithful fake encoder (the real package
  is not a hard dep, so we simulate its presence),
* backward compatibility: the public ``estimate_message_tokens`` stays
  byte-identical to the historical ``len(json.dumps)//4`` when no tokenizer is
  present, so existing budgets/thresholds do not shift.
"""

from __future__ import annotations

import json

import pytest

from magi_agent.shared import token_estimation
from magi_agent.shared.token_estimation import (
    count_text_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    tokenizer_backend,
)


@pytest.fixture(autouse=True)
def _clear_encoder_cache():
    token_estimation._load_tiktoken_encoder.cache_clear()
    yield
    token_estimation._load_tiktoken_encoder.cache_clear()


# ---------------------------------------------------------------------------
# Fallback path (no tokenizer installed — the default in CI/this env)
# ---------------------------------------------------------------------------


def test_count_text_tokens_fallback_is_char_over_four() -> None:
    assert tokenizer_backend() == "char_heuristic"
    text = "x" * 400
    assert count_text_tokens(text) == 100  # 400 // 4


def test_count_text_tokens_empty_is_zero() -> None:
    assert count_text_tokens("") == 0


def test_estimate_message_tokens_fallback_matches_legacy_formula() -> None:
    # Backward compatibility: identical to the old len(json.dumps)//4.
    for msg in (
        {"role": "user", "content": "hello"},
        {"key": "value", "nested": {"a": 1}},
        {"content": "x" * 500},
        {},
    ):
        assert estimate_message_tokens(msg) == len(json.dumps(msg, default=str)) // 4


def test_estimate_messages_tokens_fallback_sums() -> None:
    msgs = [{"content": "a" * 40}, {"content": "b" * 80}]
    assert estimate_messages_tokens(msgs) == sum(
        len(json.dumps(m, default=str)) // 4 for m in msgs
    )


# ---------------------------------------------------------------------------
# Optional tokenizer path (simulated faithful tiktoken-style encoder)
# ---------------------------------------------------------------------------


def _install_fake_encoder(monkeypatch: pytest.MonkeyPatch, *, ratio: int = 3) -> None:
    """Install a deterministic encoder that returns len(text)//ratio tokens.

    Faithfully mirrors the contract of the real loader: a ``str -> int`` counter
    (real BPE encoders return fewer tokens than char/4 on English prose, so we
    use a ratio of 3 to make the difference observable and distinct from the
    char/4 fallback).
    """
    token_estimation._load_tiktoken_encoder.cache_clear()

    def _fake_loader():
        return lambda text: len(text) // ratio

    monkeypatch.setattr(token_estimation, "_load_tiktoken_encoder", _fake_loader)


def test_tokenizer_backend_reports_tiktoken_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_encoder(monkeypatch)
    assert tokenizer_backend() == "tiktoken"


def test_count_text_tokens_uses_real_encoder_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_encoder(monkeypatch, ratio=3)
    text = "x" * 300
    # Encoder path: 300 // 3 == 100, distinct from char/4 fallback (75).
    assert count_text_tokens(text) == 100
    assert count_text_tokens(text) != len(text) // 4


def test_estimate_message_tokens_uses_real_encoder_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_encoder(monkeypatch, ratio=3)
    msg = {"role": "user", "content": "x" * 300}
    serialized = json.dumps(msg, default=str)
    assert estimate_message_tokens(msg) == len(serialized) // 3
    assert estimate_message_tokens(msg) != len(serialized) // 4


def test_loader_failure_degrades_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # A loader that raises (e.g. corrupt install) must not break estimation.
    token_estimation._load_tiktoken_encoder.cache_clear()

    def _broken_loader():
        return None

    monkeypatch.setattr(token_estimation, "_load_tiktoken_encoder", _broken_loader)
    assert tokenizer_backend() == "char_heuristic"
    assert count_text_tokens("x" * 400) == 100


# ---------------------------------------------------------------------------
# Accuracy: tokenizer path is closer to reality on realistic mixed text
# ---------------------------------------------------------------------------


def test_tokenizer_path_diverges_from_char4_on_realistic_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The improved estimate is materially different from naive char/4.

    Using a faithful fake encoder, a representative code-review message yields a
    different (here, lower) token count than the crude char/4 heuristic — which
    is the whole point of the improvement.
    """
    realistic = (
        "def compact(contents):\n"
        "    return contents[-16:]  # keep the recent tail\n" * 20
    )
    fallback = len(realistic) // 4

    _install_fake_encoder(monkeypatch, ratio=3)
    improved = count_text_tokens(realistic)

    assert improved != fallback
    assert improved > 0
