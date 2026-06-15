"""First-party ``tools_persistent_python`` pack + handler + ``harness_gaia_codeact``.

Step B (CodeAct persistent execution) as NEUTRAL packs:
  * ``tools_persistent_python`` registers a ``PersistentPython`` ToolManifest via
    the typed ``ToolProvideContext`` (D5), exactly like ``tools_clock``.
  * ``bind_persistent_python_handler`` is an additive first-party toolhost binder
    (the same layer as ``bind_core_toolhost_handlers``) whose handler runs the
    ``code`` argument in a SESSION-PERSISTENT interpreter keyed by
    ``(workspace_root, turn_id or session_id)`` — variables carry across calls.
  * ``harness_gaia_codeact`` registers a ``ResolvedHarnessPack`` whose tool
    components include ``PersistentPython`` plus a CodeAct-discipline hook.

Hermetic, no network. Mirrors ``tests/firstparty/test_tools_clock_pack.py`` +
``tests/firstparty/test_harness_coding_lean_pack.py``.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries
from magi_agent.tools.context import ToolContext
from magi_agent.tools.persistent_python_toolhost import (
    PERSISTENT_PYTHON_TOOL_NAME,
    PersistentPythonHandlerSet,
    bind_persistent_python_handler,
    register_persistent_python_manifest,
)
from magi_agent.tools.registry import ToolRegistry

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"
_TOOL_PACK = _FIRST_PARTY_ROOT / "tools_persistent_python"
_HARNESS_PACK = _FIRST_PARTY_ROOT / "harness_gaia_codeact"
_HARNESS_REF = "harness:gaia-codeact@1"


def _ctx(turn_id: str, *, session_id: str | None = None) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId=session_id,
        turnId=turn_id,
        workspaceRoot="/tmp",
    )


# --------------------------------------------------------------------------- #
# 1. Tool pack registers the PersistentPython ToolManifest with the right schema
# --------------------------------------------------------------------------- #


def test_tool_pack_registers_persistent_python_manifest() -> None:
    registries, report = load_into_registries([_TOOL_PACK])
    assert PERSISTENT_PYTHON_TOOL_NAME in report.registered
    manifest = registries.tools.resolve(PERSISTENT_PYTHON_TOOL_NAME)
    assert manifest is not None
    assert manifest.name == PERSISTENT_PYTHON_TOOL_NAME
    assert manifest.mutates_workspace is True
    assert manifest.parallel_safety == "unsafe"
    assert manifest.timeout_ms == 30_000
    assert "act" in manifest.available_in_modes
    # Input schema = a single required string ``code``.
    schema = manifest.input_schema
    assert schema["required"] == ["code"]
    assert schema["properties"]["code"]["type"] == "string"


def test_tool_pack_offered_in_act_mode() -> None:
    registries, _ = load_into_registries([_TOOL_PACK])
    act_names = {m.name for m in registries.tools.list_available(mode="act")}
    assert PERSISTENT_PYTHON_TOOL_NAME in act_names


# --------------------------------------------------------------------------- #
# 2. Handler executes code and returns stdout
# --------------------------------------------------------------------------- #


def _bound_registry() -> tuple[ToolRegistry, PersistentPythonHandlerSet | None]:
    # Discovery+projection registers the PersistentPython manifest into the live
    # ToolRegistry; the additive first-party binder attaches its handler.
    registries, _ = load_into_registries([_TOOL_PACK])
    registry = registries.tools
    handler_set = bind_persistent_python_handler(registry)
    return registry, handler_set


def _handler(registry: ToolRegistry):
    registration = registry.resolve_registration(PERSISTENT_PYTHON_TOOL_NAME)
    assert registration is not None
    assert registration.handler is not None
    return registration.handler


def test_handler_executes_code_and_returns_stdout() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    result = handler({"code": "print(2 + 2)"}, _ctx("turn-1"))
    assert result.status == "ok"
    assert "4" in str(result.output)


def test_handler_returns_last_expression_value() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    result = handler({"code": "40 + 2"}, _ctx("turn-expr"))
    assert result.status == "ok"
    assert "42" in str(result.output)


# --------------------------------------------------------------------------- #
# 3. Persistence: two sequential calls with the SAME context key share state
# --------------------------------------------------------------------------- #


def test_state_persists_across_calls_same_context() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    first = handler({"code": "x = 41"}, _ctx("turn-shared"))
    assert first.status == "ok"
    second = handler({"code": "print(x + 1)"}, _ctx("turn-shared"))
    assert second.status == "ok"
    assert "42" in str(second.output)


# --------------------------------------------------------------------------- #
# 4. Isolation: two DIFFERENT context keys do NOT share state
# --------------------------------------------------------------------------- #


def test_state_isolated_across_different_contexts() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    seeded = handler({"code": "secret = 1234"}, _ctx("turn-a"))
    assert seeded.status == "ok"
    # A different turn id must NOT see ``secret``.
    leaked = handler({"code": "print(secret)"}, _ctx("turn-b"))
    assert leaked.status == "error"
    assert "1234" not in str(leaked.output or "")


def test_session_id_keys_when_turn_id_absent() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    # No turn_id -> fall back to session_id for keying.
    first = handler({"code": "y = 7"}, _ctx("", session_id="sess-1"))
    assert first.status == "ok"
    same = handler({"code": "print(y * 2)"}, _ctx("", session_id="sess-1"))
    assert same.status == "ok"
    assert "14" in str(same.output)
    other = handler({"code": "print(y)"}, _ctx("", session_id="sess-2"))
    assert other.status == "error"


# --------------------------------------------------------------------------- #
# 5. Fail-soft on bad code + output truncation
# --------------------------------------------------------------------------- #


def test_handler_fail_soft_on_bad_code() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    # Must NOT raise out of the handler; returns a short error string.
    result = handler({"code": "1 / 0"}, _ctx("turn-err"))
    assert result.status == "error"
    assert result.error_message
    assert "ZeroDivisionError" in str(result.error_message) + str(result.output or "")


def test_handler_missing_code_is_error() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    result = handler({}, _ctx("turn-missing"))
    assert result.status == "error"


def test_handler_truncates_large_output() -> None:
    registry, _ = _bound_registry()
    handler = _handler(registry)
    result = handler(
        {"code": "print('A' * 1_000_000)"},
        _ctx("turn-big"),
    )
    assert result.status == "ok"
    stdout = _stdout(result.output)
    # Head+tail truncation: the captured output must be far smaller than 1MB and
    # carry an elision marker.
    assert len(stdout) < 200_000
    assert "elided" in stdout or "truncated" in stdout


def test_timeout_drops_interpreter_state_for_future_calls() -> None:
    registries, _ = load_into_registries([_TOOL_PACK])
    registry = registries.tools
    handler_set = PersistentPythonHandlerSet(timeout_s=0.01)
    assert bind_persistent_python_handler(registry, handler_set=handler_set) is handler_set
    handler = _handler(registry)
    ctx = _ctx("turn-timeout")

    seeded = handler({"code": "x = 41"}, ctx)
    assert seeded.status == "ok"

    timed_out = handler({"code": "import time\ntime.sleep(0.2)\nx = 99"}, ctx)
    assert timed_out.status == "error"
    assert "TimeoutError" in str(timed_out.error_message)

    fresh = handler({"code": "print(x)"}, ctx)
    assert fresh.status == "error"
    assert "NameError" in str(fresh.error_message) + str(fresh.output or "")


def _stdout(output: object) -> str:
    if isinstance(output, dict):
        return str(output.get("stdout") or output.get("value") or output)
    return str(output)


# --------------------------------------------------------------------------- #
# 6. Harness pack registers with PersistentPython in its tool components
# --------------------------------------------------------------------------- #


def test_harness_pack_registers_with_persistent_python_tool() -> None:
    registries, report = load_into_registries([_HARNESS_PACK])
    assert _HARNESS_REF in report.registered
    pack = registries.harnesses.resolve(_HARNESS_REF)
    assert pack is not None
    assert pack.enabled is True
    assert PERSISTENT_PYTHON_TOOL_NAME in pack.components["tools"]
    # A CodeAct-discipline hook is advertised.
    assert len(pack.components.get("hooks", ())) >= 1


# --------------------------------------------------------------------------- #
# 7. Pack discovery: both new packs are found by the directory-glob loader
# --------------------------------------------------------------------------- #


def test_live_catalog_discovers_persistent_python_and_harness() -> None:
    from magi_agent.packs.catalog_build import resolve_live_catalog

    live = resolve_live_catalog()
    assert PERSISTENT_PYTHON_TOOL_NAME in live.tool_refs
    assert _HARNESS_REF in live.harness_refs


# --------------------------------------------------------------------------- #
# 8. Build-path wiring is additive + gated default-OFF
# --------------------------------------------------------------------------- #


def test_cli_build_path_omits_tool_when_gate_off(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_PERSISTENT_PYTHON_ENABLED", raising=False)
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    runtime = build_cli_tool_runtime(workspace_root="/tmp", session_id="s-off")
    assert runtime.registry.resolve(PERSISTENT_PYTHON_TOOL_NAME) is None


def test_cli_build_path_registers_and_binds_tool_when_gate_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_PERSISTENT_PYTHON_ENABLED", "1")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    runtime = build_cli_tool_runtime(workspace_root="/tmp", session_id="s-on")
    registration = runtime.registry.resolve_registration(PERSISTENT_PYTHON_TOOL_NAME)
    assert registration is not None
    assert registration.handler is not None
    assert registration.enabled is True


def test_direct_manifest_registration_respects_pack_disable(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[packs]\ndisable = ["open' 'magi.tools-persistent-python"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))

    registry = ToolRegistry()
    register_persistent_python_manifest(registry)

    assert registry.resolve(PERSISTENT_PYTHON_TOOL_NAME) is None
    assert bind_persistent_python_handler(registry) is None


def test_cli_build_path_respects_pack_disable_when_gate_on(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[packs]\ndisable = ["open' 'magi.tools-persistent-python"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGI_CONFIG", str(cfg))
    monkeypatch.setenv("MAGI_PERSISTENT_PYTHON_ENABLED", "1")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    runtime = build_cli_tool_runtime(workspace_root="/tmp", session_id="s-disabled")
    assert runtime.registry.resolve(PERSISTENT_PYTHON_TOOL_NAME) is None
