"""E-15 — per-knob parity: registry-driven reads match the legacy inline reads.

The 3 model-knob builders (``_model_retry_kwargs``,
``_model_reasoning_kwargs``, ``_model_api_base_kwargs``) used to read 8
``MAGI_*`` knobs inline via ``source.get(...)`` + ad-hoc parsing. They
now route through ``flag_int``/``flag_str``. Behavior must stay
byte-identical: same env → same kwargs dict.

The parity sweep also locks in the *parse semantics* of each knob:
positive-int with defaults, empty-string-as-unset, disable tokens for
reasoning effort, and the ``x-api-key`` default for the API header.
"""

from __future__ import annotations

import pytest

from magi_agent.cli import real_runner


# ---------------------------------------------------------------------------
# _model_retry_kwargs — MAGI_MODEL_NUM_RETRIES, MAGI_MODEL_TIMEOUT_S (int)
# ---------------------------------------------------------------------------


def test_retry_kwargs_unset_uses_registry_defaults() -> None:
    out = real_runner._model_retry_kwargs(env={})
    assert out == {"num_retries": 4, "timeout": 600}


def test_retry_kwargs_explicit_values() -> None:
    out = real_runner._model_retry_kwargs(
        env={"MAGI_MODEL_NUM_RETRIES": "7", "MAGI_MODEL_TIMEOUT_S": "30"}
    )
    assert out == {"num_retries": 7, "timeout": 30}


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "  ", "abc", "  -5  "],
)
def test_retry_kwargs_non_positive_or_invalid_falls_back(value: str) -> None:
    """Non-positive / unparseable values must fall back to the default,
    not break the build with ``num_retries=0`` (which retries 0 times)."""

    out = real_runner._model_retry_kwargs(
        env={"MAGI_MODEL_NUM_RETRIES": value, "MAGI_MODEL_TIMEOUT_S": value}
    )
    assert out == {"num_retries": 4, "timeout": 600}


def test_retry_kwargs_whitespace_trimmed() -> None:
    out = real_runner._model_retry_kwargs(
        env={"MAGI_MODEL_NUM_RETRIES": "  10  ", "MAGI_MODEL_TIMEOUT_S": "  120  "}
    )
    assert out == {"num_retries": 10, "timeout": 120}


# ---------------------------------------------------------------------------
# _model_reasoning_kwargs — MAGI_MODEL_THINKING_TYPE (str),
# MAGI_MODEL_THINKING_BUDGET_TOKENS (int), MAGI_MODEL_REASONING_EFFORT (str)
# ---------------------------------------------------------------------------


def test_reasoning_kwargs_thinking_type_adaptive_wins() -> None:
    out = real_runner._model_reasoning_kwargs(
        env={
            "MAGI_MODEL_THINKING_TYPE": "adaptive",
            # These two are overridden by THINKING_TYPE having higher precedence.
            "MAGI_MODEL_THINKING_BUDGET_TOKENS": "8000",
            "MAGI_MODEL_REASONING_EFFORT": "high",
        }
    )
    assert out == {"thinking": {"type": "adaptive"}}


def test_reasoning_kwargs_thinking_type_case_insensitive() -> None:
    out = real_runner._model_reasoning_kwargs(env={"MAGI_MODEL_THINKING_TYPE": "ADAPTIVE"})
    assert out == {"thinking": {"type": "adaptive"}}


def test_reasoning_kwargs_thinking_type_other_value_ignored() -> None:
    """Only ``adaptive`` activates the escape hatch. Other values fall
    through to the budget/effort precedence ladder."""

    out = real_runner._model_reasoning_kwargs(
        env={"MAGI_MODEL_THINKING_TYPE": "manual", "MAGI_MODEL_REASONING_EFFORT": "high"}
    )
    assert out == {"reasoning_effort": "high"}


def test_reasoning_kwargs_budget_tokens_explicit() -> None:
    out = real_runner._model_reasoning_kwargs(
        env={"MAGI_MODEL_THINKING_BUDGET_TOKENS": "8000"}
    )
    assert out == {"thinking": {"type": "enabled", "budget_tokens": 8000}}


@pytest.mark.parametrize("value", ["0", "-100", "abc", "  "])
def test_reasoning_kwargs_budget_tokens_invalid_falls_through(value: str) -> None:
    """Non-positive / unparseable budget values fall through to the
    next precedence layer (here: empty env ⇒ ``{}``)."""

    out = real_runner._model_reasoning_kwargs(
        env={"MAGI_MODEL_THINKING_BUDGET_TOKENS": value}
    )
    assert out == {}


def test_reasoning_kwargs_effort_string() -> None:
    out = real_runner._model_reasoning_kwargs(env={"MAGI_MODEL_REASONING_EFFORT": "high"})
    assert out == {"reasoning_effort": "high"}


@pytest.mark.parametrize(
    "kill_token", ["off", "none", "0", "false", "disable", "disabled"]
)
def test_reasoning_kwargs_effort_kill_switch(kill_token: str) -> None:
    out = real_runner._model_reasoning_kwargs(
        env={"MAGI_MODEL_REASONING_EFFORT": kill_token}
    )
    assert out == {}


def test_reasoning_kwargs_empty_env() -> None:
    out = real_runner._model_reasoning_kwargs(env={})
    assert out == {}


# ---------------------------------------------------------------------------
# _model_api_base_kwargs — MAGI_LLM_API_BASE / KEY / HEADER (str)
# ---------------------------------------------------------------------------


def test_api_base_kwargs_unset_returns_empty() -> None:
    out = real_runner._model_api_base_kwargs(env={})
    assert out == {}


def test_api_base_kwargs_base_only() -> None:
    out = real_runner._model_api_base_kwargs(
        env={"MAGI_LLM_API_BASE": "https://gateway.example/v1"}
    )
    assert out == {"api_base": "https://gateway.example/v1"}


def test_api_base_kwargs_base_plus_key_default_header() -> None:
    """``MAGI_LLM_API_HEADER`` defaults to ``x-api-key`` even when the
    env literally does not set it (catches a registry-default leak)."""

    out = real_runner._model_api_base_kwargs(
        env={
            "MAGI_LLM_API_BASE": "https://gateway.example/v1",
            "MAGI_LLM_API_KEY": "secret-token",
        }
    )
    assert out == {
        "api_base": "https://gateway.example/v1",
        "api_key": "secret-token",
        "extra_headers": {"x-api-key": "secret-token"},
    }


def test_api_base_kwargs_explicit_header_overrides_default() -> None:
    out = real_runner._model_api_base_kwargs(
        env={
            "MAGI_LLM_API_BASE": "https://gateway.example/v1",
            "MAGI_LLM_API_KEY": "secret-token",
            "MAGI_LLM_API_HEADER": "Authorization",
        }
    )
    assert out["extra_headers"] == {"Authorization": "secret-token"}


def test_api_base_kwargs_empty_header_falls_back_to_default() -> None:
    """An explicit empty string for the header must NOT be sent as-is —
    fall back to ``x-api-key`` (preserves legacy parse behavior)."""

    out = real_runner._model_api_base_kwargs(
        env={
            "MAGI_LLM_API_BASE": "https://gateway.example/v1",
            "MAGI_LLM_API_KEY": "secret-token",
            "MAGI_LLM_API_HEADER": "",
        }
    )
    assert out["extra_headers"] == {"x-api-key": "secret-token"}


def test_api_base_kwargs_empty_base_returns_empty() -> None:
    """An explicit empty base disables the gateway, even if KEY/HEADER
    are set."""

    out = real_runner._model_api_base_kwargs(
        env={
            "MAGI_LLM_API_BASE": "",
            "MAGI_LLM_API_KEY": "secret-token",
            "MAGI_LLM_API_HEADER": "Authorization",
        }
    )
    assert out == {}


def test_api_base_kwargs_whitespace_trimmed() -> None:
    out = real_runner._model_api_base_kwargs(
        env={
            "MAGI_LLM_API_BASE": "  https://gateway.example/v1  ",
            "MAGI_LLM_API_KEY": "  k  ",
            "MAGI_LLM_API_HEADER": "  x-custom  ",
        }
    )
    assert out["api_base"] == "https://gateway.example/v1"
    assert out["api_key"] == "k"
    assert out["extra_headers"] == {"x-custom": "k"}
