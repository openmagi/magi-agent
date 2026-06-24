"""E-15 — assert every ``cli/real_runner.py`` model knob is a typed
flag in the registry.

REVIEW-A (llm L3 / CC-4) flagged that the model knobs
(``MAGI_MODEL_NUM_RETRIES``, ``MAGI_MODEL_TIMEOUT_S``,
``MAGI_MODEL_THINKING_TYPE``, ``MAGI_MODEL_THINKING_BUDGET_TOKENS``,
``MAGI_MODEL_REASONING_EFFORT``, ``MAGI_LLM_API_BASE``,
``MAGI_LLM_API_KEY``, ``MAGI_LLM_API_HEADER``) used to read ``os.environ``
inline with ad-hoc parse logic, so nothing enumerated "what knobs
affect the model".

The migration to ``flag_int`` / ``flag_str`` already landed at every
read site; this meta-test locks the invariant. A future regression
that re-introduces a raw ``os.environ`` read for one of these knobs OR
drops a ``FlagSpec`` from the registry fails LOUDLY at test time
instead of silently dropping the knob from discovery.

Per the plan, ``MAGI_LLM_API_KEY`` is the sensitive one — it must be
typed but its *runtime value* must not leak through any discovery
projection (covered separately by the redaction kernel; here we only
assert registration + kind).
"""

from __future__ import annotations

import pytest

from magi_agent.config.flags import FLAGS_BY_NAME, get_flag


#: The eight model knobs E-15 names, with the *kind* they must register
#: as in the typed flag registry. Values come from the plan; if a
#: future PR changes a knob's kind it must update this table in the
#: same commit (and explain why in the commit message).
_E15_MODEL_KNOBS: dict[str, str] = {
    "MAGI_MODEL_NUM_RETRIES": "int",
    "MAGI_MODEL_TIMEOUT_S": "int",
    "MAGI_MODEL_THINKING_TYPE": "str",
    "MAGI_MODEL_THINKING_BUDGET_TOKENS": "int",
    "MAGI_MODEL_REASONING_EFFORT": "str",
    "MAGI_LLM_API_BASE": "str",
    "MAGI_LLM_API_KEY": "str",
    "MAGI_LLM_API_HEADER": "str",
}


@pytest.mark.parametrize(
    "knob,expected_kind",
    sorted(_E15_MODEL_KNOBS.items()),
)
def test_model_knob_registered_with_expected_kind(
    knob: str, expected_kind: str
) -> None:
    """Each knob must appear in ``FLAGS_BY_NAME`` with the expected ``kind``."""

    assert knob in FLAGS_BY_NAME, (
        f"{knob} is missing from the typed flag registry. "
        "Register it as a ``FlagSpec`` in ``magi_agent/config/flags.py`` "
        "(E-15) so it appears in flag discovery."
    )
    spec = get_flag(knob)
    assert spec.kind == expected_kind, (
        f"{knob} registered with kind {spec.kind!r}; "
        f"expected {expected_kind!r} per E-15"
    )


def test_int_knobs_have_int_defaults() -> None:
    """``flag_int`` only returns the registered default when it
    ``isinstance(spec.default, int)`` (I-11 narrowing). Any int knob
    registered with a non-int default would silently surface as ``None``
    at runtime — the registration must match the reader contract."""

    for knob, expected_kind in _E15_MODEL_KNOBS.items():
        if expected_kind != "int":
            continue
        spec = get_flag(knob)
        # Reject bool too (bool ⊂ int in Python; the I-11 narrowing
        # explicitly rejects bool defaults for int knobs).
        assert isinstance(spec.default, int) and not isinstance(
            spec.default, bool
        ), (
            f"{knob} kind=int but default={spec.default!r} is not a plain int — "
            "flag_int would defensively return None at runtime"
        )


def test_str_knobs_have_str_defaults() -> None:
    """Same parity for str knobs vs ``flag_str`` (I-11)."""

    for knob, expected_kind in _E15_MODEL_KNOBS.items():
        if expected_kind != "str":
            continue
        spec = get_flag(knob)
        assert isinstance(spec.default, str), (
            f"{knob} kind=str but default={spec.default!r} is not a str — "
            "flag_str would defensively return None at runtime"
        )


def test_real_runner_reads_use_flag_registry_not_raw_environ() -> None:
    """Meta-test on ``cli/real_runner.py`` source: every E-15 knob name
    that appears in the file must do so inside a ``flag_int``/``flag_str``
    call, NOT a bare ``os.environ.get`` / ``os.getenv`` / ``source.get``
    call. This forbids a regression that re-routes one of the knobs
    around the registry."""

    from pathlib import Path

    import magi_agent

    src = (
        Path(magi_agent.__file__).parent / "cli" / "real_runner.py"
    ).read_text(encoding="utf-8")

    offenders: list[str] = []
    for knob in _E15_MODEL_KNOBS:
        # Find every line mentioning the knob name.
        for idx, line in enumerate(src.splitlines(), 1):
            if knob not in line:
                continue
            # Allow docstring/comment mentions.
            stripped = line.lstrip()
            if stripped.startswith(("#", "*", "'", '"')) or "``" in line:
                continue
            # Allow lines that route through the typed flag registry.
            if "flag_int(" in line or "flag_str(" in line:
                continue
            # Allow the helper definition itself (``def _positive(name, default)``
            # forwards through ``flag_int``); knobs inside its caller are the
            # interesting ones, not the helper body.
            if "def _positive" in line:
                continue
            # Anything else is a raw read — flag it.
            if any(
                pattern in line
                for pattern in (
                    "os.environ.get",
                    "os.environ[",
                    "os.getenv(",
                    "source.get(",
                    "env.get(",
                )
            ):
                offenders.append(f"line {idx}: {stripped[:120]}")
    assert offenders == [], (
        "A raw env read for an E-15 model knob has been re-introduced "
        "in cli/real_runner.py. Route through the typed flag registry "
        f"(``flag_int`` / ``flag_str``) instead. Offenders:\n"
        + "\n".join(offenders)
    )
