"""N-33 lock: the cron field parser lives in exactly one leaf home.

``_parse_cron_field`` used to be byte-for-byte duplicated between
``magi_agent/missions/cron_policy.py`` and
``magi_agent/harness/cron_runtime.py`` (with a third private cross-module
import from ``missions/schedule_grammar.py``). H2 extracts the body verbatim
into the stdlib-only leaf ``magi_agent/shared/cron_fields.py`` as the public
``parse_cron_field`` and routes every consumer through it (back-compat
``_parse_cron_field`` aliases preserved). These tests pin:

1. the two historic call sites resolve to the SAME function object,
2. exactly one ``def`` of the parser exists in the whole tree (the leaf),
3. the extracted behaviour is unchanged (parity table).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from magi_agent.shared.cron_fields import parse_cron_field

PACKAGE = Path(__file__).resolve().parents[1] / "magi_agent"


def test_cron_parser_is_single_shared_object() -> None:
    import magi_agent.harness.cron_runtime as cron_runtime
    import magi_agent.missions.cron_policy as cron_policy

    assert cron_policy._parse_cron_field is cron_runtime._parse_cron_field
    assert cron_policy._parse_cron_field is parse_cron_field


def test_only_one_cron_parser_definition_in_tree() -> None:
    definitions: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_parse_cron_field",
                "parse_cron_field",
            }:
                definitions.append(path.relative_to(PACKAGE).as_posix())
    assert definitions == ["shared/cron_fields.py"], (
        "The cron field parser must be defined exactly once, in "
        "magi_agent/shared/cron_fields.py. Found definitions in: "
        f"{sorted(definitions)}"
    )


def test_parse_cron_field_behaviour_table() -> None:
    assert parse_cron_field("*", 0, 59) == frozenset(range(0, 60))
    assert parse_cron_field("*/15", 0, 59) == frozenset({0, 15, 30, 45})
    assert parse_cron_field("1-5", 0, 59) == frozenset({1, 2, 3, 4, 5})
    assert parse_cron_field("1-5/2", 0, 59) == frozenset({1, 3, 5})
    assert parse_cron_field("0,30", 0, 59) == frozenset({0, 30})


def test_parse_cron_field_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="cron value out of range"):
        parse_cron_field("61", 0, 59)


def test_parse_cron_field_rejects_non_positive_step() -> None:
    with pytest.raises(ValueError, match="cron step must be positive"):
        parse_cron_field("*/0", 0, 59)


def test_parse_cron_field_rejects_empty_field() -> None:
    # An empty field raises: the bare empty token routes to ``int("")`` and
    # raises before the ``cron field cannot be empty`` guard, so we only pin
    # that some ValueError surfaces (parity with the historic body).
    with pytest.raises(ValueError):
        parse_cron_field("", 0, 59)
