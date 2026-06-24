"""I-11 — ``flag_str`` / ``flag_int`` narrow the union default at the
reader boundary so the ``# type: ignore[return-value]`` lines disappear.

REVIEW-A config L3 (I-11) flagged the only three ``# type: ignore``s in
``config/flags.py``: ``flag_str`` returned ``spec.default  # type:
ignore[return-value]`` (line ~2405) and ``flag_int`` repeated the same
pattern twice (lines ~2417, 2421). Root cause: a single
``FlagSpec.default: str | bool | int | None`` union serves four flag
kinds, so the reader could not statically reason about the type of the
default at return time.

I-11 Option A (the plan's recommendation) keeps the schema unchanged and
narrows at the *reader* via the kind invariant: after the ``spec.kind``
gate, the reader does an ``isinstance`` check against the expected
concrete type and returns ``None`` when a mis-registered default does
not match. This module locks the post-fix contract:

1. ``flag_str`` returns the registered ``str`` default when unset.
2. ``flag_int`` returns the registered ``int`` default when unset.
3. ``flag_int`` returns the registered default on a malformed env value.
4. ``flag_int`` rejects a mis-registered ``bool`` default (``bool`` is a
   subclass of ``int`` — without the explicit guard a ``bool`` flag's
   default would silently leak through ``flag_int``).
5. The ``# type: ignore[return-value]`` markers are gone from the two
   readers (meta-test).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import magi_agent
from magi_agent.config.flags import flag_int, flag_str, get_flag


# ---------------------------------------------------------------------------
# Behavioural contracts — unset / set / malformed / kind-mismatch.
# ---------------------------------------------------------------------------


def test_flag_str_returns_registered_default_when_unset() -> None:
    """Pick any registered ``str`` flag and confirm unset reads its default."""

    # ``MAGI_STREAM_FALLBACK_MODEL`` is a str flag registered with a known
    # default. Reading it with no env override must hit that default.
    spec = get_flag("MAGI_STREAM_FALLBACK_MODEL")
    assert spec.kind == "str"
    assert isinstance(spec.default, str)
    assert flag_str("MAGI_STREAM_FALLBACK_MODEL", env={}) == spec.default


def test_flag_str_returns_explicit_env_value_over_default() -> None:
    assert (
        flag_str(
            "MAGI_STREAM_FALLBACK_MODEL", env={"MAGI_STREAM_FALLBACK_MODEL": "claude-fable-5"}
        )
        == "claude-fable-5"
    )


def test_flag_str_rejects_non_str_kind() -> None:
    with pytest.raises(TypeError):
        # ``MAGI_CLI_ENABLED`` is a bool flag — calling ``flag_str`` on it
        # is a kind-mismatch and must raise.
        flag_str("MAGI_CLI_ENABLED")


def test_flag_int_returns_registered_default_when_unset() -> None:
    # ``MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT`` is an int flag
    # registered with a 0 default (the in-module clamp logic owns the
    # real floor — see I-4 batch 16). Reading unset must hit the default.
    spec = get_flag("MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT")
    assert spec.kind == "int"
    assert isinstance(spec.default, int)
    assert (
        flag_int("MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT", env={}) == spec.default
    )


def test_flag_int_parses_explicit_env_value() -> None:
    assert (
        flag_int(
            "MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT",
            env={"MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT": "128"},
        )
        == 128
    )


def test_flag_int_falls_back_to_default_on_malformed() -> None:
    spec = get_flag("MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT")
    assert (
        flag_int(
            "MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT",
            env={"MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT": "not-a-number"},
        )
        == spec.default
    )


def test_flag_int_rejects_non_int_kind() -> None:
    with pytest.raises(TypeError):
        flag_int("MAGI_CLI_ENABLED")


# ---------------------------------------------------------------------------
# Defensive narrowing — a mis-registered default falls back to ``None``.
# ---------------------------------------------------------------------------


def test_flag_str_defensively_returns_none_when_default_is_not_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a flag is ever mis-registered (an ``int`` default in a ``str``
    slot, etc.), the reader must NOT leak the wrong type — it surfaces
    as ``None`` (caller treats it as "unset")."""

    from magi_agent.config import flags as flags_mod

    fake_spec = type(
        "FakeSpec",
        (),
        {"name": "MAGI_FAKE_STR_BUT_INT_DEFAULT", "kind": "str", "default": 42},
    )()
    monkeypatch.setattr(flags_mod, "get_flag", lambda name: fake_spec)
    assert flag_str("MAGI_FAKE_STR_BUT_INT_DEFAULT", env={}) is None


def test_flag_int_defensively_rejects_bool_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bool`` is a subclass of ``int`` in Python. Without an explicit
    guard ``isinstance(spec.default, int)`` would silently let a
    ``bool`` default leak through ``flag_int``. The narrowing must
    reject it."""

    from magi_agent.config import flags as flags_mod

    fake_spec = type(
        "FakeSpec",
        (),
        {"name": "MAGI_FAKE_INT_BUT_BOOL_DEFAULT", "kind": "int", "default": True},
    )()
    monkeypatch.setattr(flags_mod, "get_flag", lambda name: fake_spec)
    assert flag_int("MAGI_FAKE_INT_BUT_BOOL_DEFAULT", env={}) is None


# ---------------------------------------------------------------------------
# Meta-test: the ``# type: ignore[return-value]`` markers are gone.
# ---------------------------------------------------------------------------


def test_no_type_ignore_in_flag_readers() -> None:
    """The whole point of I-11 Option A was to delete the three
    ``# type: ignore[return-value]`` markers from ``flag_str`` /
    ``flag_int``. Forbid a regression that re-adds them inside those
    two function bodies."""

    src = (Path(magi_agent.__file__).parent / "config" / "flags.py").read_text(
        encoding="utf-8"
    )
    lines = src.splitlines()
    offenders: list[tuple[int, str]] = []
    in_target = False
    target_name = ""
    target_indent = 0
    for idx, line in enumerate(lines, 1):
        stripped = line.lstrip()
        m = re.match(r"def\s+(flag_str|flag_int)\s*\(", stripped)
        if m:
            in_target = True
            target_name = m.group(1)
            target_indent = len(line) - len(stripped)
            continue
        if in_target:
            current_indent = len(line) - len(line.lstrip())
            if stripped and current_indent <= target_indent and not stripped.startswith(
                "def "
            ):
                in_target = False
                continue
            # Skip the docstring lines that legitimately *mention* the
            # historical ``# type: ignore`` marker in prose.
            if "# type: ignore" in stripped and not stripped.lstrip().startswith(
                ("``", "#", '"')
            ):
                if "type: ignore" in stripped and not (
                    "``# type: ignore" in stripped or "historical" in stripped.lower()
                ):
                    offenders.append((idx, f"{target_name}: {stripped[:80]}"))
    assert offenders == [], (
        "I-11 narrowing was supposed to delete every ``# type: ignore`` "
        "from the flag_str / flag_int bodies. A regression has re-added "
        f"one. Offenders: {offenders}"
    )
