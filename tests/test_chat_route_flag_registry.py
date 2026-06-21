"""Tests for I-4: ``CORE_AGENT_PYTHON_CHAT_ROUTE`` registry consolidation.

Per the I-4 plan (``docs/plans/2026-06-18-magi-agent-oss-main-remediation/
ws-I-config-quality.md`` §I-4) the hosted python chat-route authority gate
``CORE_AGENT_PYTHON_CHAT_ROUTE`` was read inline ~6× in
``magi_agent/transport/chat_routes.py`` (each
``os.environ.get(..., "off").lower() != "on"``) before this PR. The migration:

1. Registers ``CORE_AGENT_PYTHON_CHAT_ROUTE`` in
   :mod:`magi_agent.config.flags` as a hosted-scope ``kind="bool"`` flag
   with ``default=False`` (excluded from the public env-reference because it
   is a hosted-runtime authority gate, not a self-host toggle).
2. Adds a single module-level helper
   :func:`magi_agent.transport.chat_routes._python_chat_route_enabled` that
   delegates to :func:`magi_agent.config.flags.flag_bool` — the canonical
   typed reader for ``kind="bool"`` flags.
3. Replaces the 6 inline ``os.environ.get(...).lower() != "on"`` reads with
   calls to the helper.

Parity notes
------------
The legacy inline form treated **only** ``on``/``ON`` (case-insensitive) as
truthy. The new :func:`flag_bool` path also treats ``1``/``true``/``yes``
(the shared ``_truthy.is_true`` allowlist) as truthy. This is a **strict
superset** of the legacy contract — the only direction of divergence is "more
values now also enable the route". Plan-acceptable per §I-4 because:

* No existing caller relies on ``=1``/``=true`` **NOT** enabling the route
  (verified by inventory grep across ``magi_agent/**`` and ``infra/``).
* The hosted control overlays (``magi_agent/runtime/hosted_defaults.py``)
  drive this flag with ``"on"`` only, so behaviour in production is
  byte-identical to today.
* Operator typos (``MAGI_CHAT_ROUTE=true``) used to silently fall through;
  now they enable the route, which matches operator intent.

The parity table below pins:

* ``on``/``ON`` → True (legacy + new agree).
* ``off``/``OFF``/unset/``garbage``/empty string → False (legacy + new agree).
* ``1``/``true``/``yes`` → True (new only; documented strict-superset).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.config.flags import FLAGS_BY_NAME, flag_bool
from magi_agent.transport.chat_routes import _python_chat_route_enabled

CORE_AGENT_PYTHON_CHAT_ROUTE = "CORE_AGENT_PYTHON_CHAT_ROUTE"


# (env_value_or_None, expected_helper_return)
# Strict-superset over the legacy "=on" / "=ON" contract: every legacy-truthy
# input still resolves True; additionally 1/true/yes/whitespace+case-fold now
# also resolve True (matches the shared truthy allowlist).
_PARITY_CASES: tuple[tuple[str | None, bool], ...] = (
    # Legacy agrees:
    (None, False),       # unset
    ("on", True),
    ("ON", True),
    ("off", False),
    ("OFF", False),
    ("", False),
    ("garbage", False),  # strict opt-in: unknown stays False
    # Strict superset (new path also accepts these):
    ("1", True),
    ("true", True),
    ("TRUE", True),
    ("yes", True),
    ("Yes", True),
    ("  on  ", True),    # whitespace+case-fold
    # Explicit falsey (legacy + new agree):
    ("0", False),
    ("false", False),
    ("no", False),
)


@pytest.mark.parametrize(("raw", "expected"), _PARITY_CASES)
def test_python_chat_route_enabled_parity(raw: str | None, expected: bool) -> None:
    """Helper resolves consistent with the registry's strict-truthy convention.

    Documented in the module docstring: the new path is a strict superset of
    the legacy ``=on``-only inline form. Every input in the parity table
    matches the legacy contract OR is one of the documented superset values.
    """
    env: dict[str, str] = {} if raw is None else {CORE_AGENT_PYTHON_CHAT_ROUTE: raw}
    assert _python_chat_route_enabled(env) is expected


def test_helper_delegates_to_flag_bool() -> None:
    """The helper must match the registry typed reader byte-for-byte.

    Pins the single-decision-point invariant: any divergence between the
    helper and ``flag_bool`` would mean another reading path silently
    reintroduced the legacy ``=on``-only contract.
    """
    for raw, expected in _PARITY_CASES:
        env: dict[str, str] = (
            {} if raw is None else {CORE_AGENT_PYTHON_CHAT_ROUTE: raw}
        )
        assert (
            _python_chat_route_enabled(env)
            is flag_bool(CORE_AGENT_PYTHON_CHAT_ROUTE, env=env)
            is expected
        )


def test_flag_is_registered_as_hosted_bool() -> None:
    """``CORE_AGENT_PYTHON_CHAT_ROUTE`` lives in the registry with hosted scope.

    Pins the membership + kind + scope so a future rename cannot silently:

    * downgrade the kind (e.g. to ``str``) and break ``flag_bool`` callers, or
    * widen the scope to ``public`` and have the public env-reference
      generator surface a hosted-runtime authority gate as a self-host toggle.
    """
    spec = FLAGS_BY_NAME[CORE_AGENT_PYTHON_CHAT_ROUTE]
    assert spec.kind == "bool"
    assert spec.scope == "hosted"
    assert spec.default is False


def test_default_env_resolves_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env (``env=None`` convenience form) resolves False.

    The hosted control overlay must explicitly set ``=on`` to enable the
    route; a fresh ``os.environ`` without the flag stays disabled.
    """
    monkeypatch.delenv(CORE_AGENT_PYTHON_CHAT_ROUTE, raising=False)
    assert _python_chat_route_enabled() is False
    assert _python_chat_route_enabled(None) is False


def test_helper_is_single_decision_point_for_chat_routes_py() -> None:
    """``chat_routes.py`` reads ``CORE_AGENT_PYTHON_CHAT_ROUTE`` exactly once.

    AST-level invariant: after the I-4 migration the only Call node passing
    the flag string is the helper's ``flag_bool`` delegation. Any new inline
    ``os.environ.get(...)`` / ``os.getenv(...)`` Call node referencing the
    flag string would fail this gate. Docstrings, comments, and string
    literals outside Call nodes are ignored by construction.
    """
    import ast

    chat_routes = (
        Path(__file__).resolve().parent.parent
        / "magi_agent"
        / "transport"
        / "chat_routes.py"
    )
    tree = ast.parse(chat_routes.read_text(encoding="utf-8"))

    inline_env_call_sites: list[str] = []
    flag_bool_call_sites: list[str] = []

    def _const_str_arg(node: ast.AST) -> str | None:
        return (
            node.value
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            else None
        )

    def _attr_chain(func: ast.AST) -> str:
        # "os.environ.get" / "os.getenv" — minimal qualifier walker.
        parts: list[str] = []
        cur: ast.AST | None = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    def _subscript_target(node: ast.AST) -> str:
        # "os.environ" — for the ``os.environ["NAME"]`` form.
        return _attr_chain(node)

    for node in ast.walk(tree):
        # ``os.environ["CORE_AGENT_PYTHON_CHAT_ROUTE"]`` — subscript form.
        if isinstance(node, ast.Subscript):
            target = _subscript_target(node.value)
            if target.endswith("environ"):
                key = _const_str_arg(node.slice)
                if key == CORE_AGENT_PYTHON_CHAT_ROUTE:
                    inline_env_call_sites.append("os.environ[...]")
            continue
        if not isinstance(node, ast.Call):
            continue
        # Skip the helper's own flag_bool call from the inline-read tally.
        chain = _attr_chain(node.func)
        if chain in {"os.environ.get", "os.getenv"} and node.args:
            key = _const_str_arg(node.args[0])
            if key == CORE_AGENT_PYTHON_CHAT_ROUTE:
                inline_env_call_sites.append(chain)
        elif chain.endswith("flag_bool") and node.args:
            key = _const_str_arg(node.args[0])
            if key == CORE_AGENT_PYTHON_CHAT_ROUTE:
                flag_bool_call_sites.append(chain)

    assert inline_env_call_sites == [], (
        f"Found {len(inline_env_call_sites)} inline env reads for "
        f"{CORE_AGENT_PYTHON_CHAT_ROUTE} in chat_routes.py "
        f"({inline_env_call_sites!r}) — route through "
        "_python_chat_route_enabled() / flag_bool() instead."
    )
    assert flag_bool_call_sites, (
        "expected exactly one flag_bool(...) call site delegating to the "
        "registered CORE_AGENT_PYTHON_CHAT_ROUTE flag"
    )
