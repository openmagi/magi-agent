"""N-36 lock: the boolean resolution trio lives in exactly one leaf home.

``coerce_bool`` / ``_override_bool`` / ``_resolve_bool`` used to be
byte-for-byte duplicated between ``magi_agent/memory/config.py`` and
``magi_agent/knowledge/okf/config.py`` (both also re-declaring the
``_TRUE_VALUES`` / ``_FALSE_VALUES`` sets that already live in the canonical
``magi_agent/config/_truthy.py`` leaf). H2 extracts the trio into
``magi_agent/config/_bool_resolution.py`` (which imports the value sets from
``config/_truthy``) and routes both config modules through it (back-compat
``_override_bool`` / ``_resolve_bool`` aliases + ``coerce_bool`` re-export
preserved). These tests pin:

1. every consumer resolves to the SAME function objects,
2. exactly one ``def coerce_bool`` exists in the tree (the leaf),
3. the extracted behaviour is unchanged (parity table).
"""

from __future__ import annotations

import ast
from pathlib import Path

import magi_agent.config._bool_resolution as bool_resolution
import magi_agent.knowledge.okf.config as okf_config
import magi_agent.memory.config as memory_config

PACKAGE = Path(__file__).resolve().parents[1] / "magi_agent"


def test_bool_trio_is_single_shared_object() -> None:
    assert (
        memory_config.coerce_bool
        is okf_config.coerce_bool
        is bool_resolution.coerce_bool
    )
    assert (
        memory_config._override_bool
        is okf_config._override_bool
        is bool_resolution.override_bool
    )
    assert (
        memory_config._resolve_bool
        is okf_config._resolve_bool
        is bool_resolution.resolve_bool
    )


def test_only_one_coerce_bool_definition_in_tree() -> None:
    definitions: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "coerce_bool":
                definitions.append(path.relative_to(PACKAGE).as_posix())
    assert definitions == ["config/_bool_resolution.py"], (
        "coerce_bool must be defined exactly once, in "
        "magi_agent/config/_bool_resolution.py. Found definitions in: "
        f"{sorted(definitions)}"
    )


def test_coerce_bool_behaviour_table() -> None:
    coerce_bool = bool_resolution.coerce_bool
    assert coerce_bool(True) is True
    assert coerce_bool(False) is False
    for truthy in ("1", "true", "yes", "on", "True", "  ON  "):
        assert coerce_bool(truthy) is True
    for falsey in ("0", "false", "no", "off", ""):
        assert coerce_bool(falsey) is False
    assert coerce_bool(None) is None
    assert coerce_bool("garbage") is None


def test_override_bool_env_beats_config_and_skips_invalid() -> None:
    override_bool = bool_resolution.override_bool
    # env wins over config
    assert override_bool(
        {"FLAG": "1"}, {"flag": "0"}, env_var="FLAG", config_key="flag"
    ) is True
    # config used when env unset
    assert override_bool(
        {}, {"flag": "off"}, env_var="FLAG", config_key="flag"
    ) is False
    # invalid env value falls through to config
    assert override_bool(
        {"FLAG": "garbage"}, {"flag": "on"}, env_var="FLAG", config_key="flag"
    ) is True
    # nothing set -> None
    assert override_bool({}, {}, env_var="FLAG", config_key="flag") is None


def test_resolve_bool_default_path() -> None:
    resolve_bool = bool_resolution.resolve_bool
    assert resolve_bool(
        {}, {}, env_var="FLAG", config_key="flag", default=True
    ) is True
    assert resolve_bool(
        {}, {}, env_var="FLAG", config_key="flag", default=False
    ) is False
    assert resolve_bool(
        {"FLAG": "0"}, {}, env_var="FLAG", config_key="flag", default=True
    ) is False
