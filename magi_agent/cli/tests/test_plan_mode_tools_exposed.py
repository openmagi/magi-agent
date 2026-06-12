"""CLI exposure of the manifest-routed plan-mode tools (doc 12 PR2).

Proves the doc's completion metric: with ``MAGI_PLAN_MODE_TOOLS_ENABLED`` ON the
``AskUserQuestion`` / ``EnterPlanMode`` / ``ExitPlanMode`` tools are no longer
silently filtered by the ``cli/wiring.py`` ``handler is not None`` filter and
appear in the CLI tool set; with the gate OFF (default) they are NOT advertised,
byte-identical to ``main``.
"""
from __future__ import annotations

from magi_agent.cli.tool_runtime import build_cli_adk_tools, build_cli_tool_runtime


_PLAN_MODE_TOOLS = {"AskUserQuestion", "EnterPlanMode", "ExitPlanMode"}


def _tool_names(tools: list[object]) -> set[str]:
    return {getattr(tool, "name", None) for tool in tools}


def test_plan_mode_tools_hidden_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MAGI_PLAN_MODE_TOOLS_ENABLED", raising=False)

    tools = build_cli_adk_tools(workspace_root=str(tmp_path), mode="plan")
    names = _tool_names(tools)

    assert not (_PLAN_MODE_TOOLS & names)


def test_plan_mode_tools_exposed_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_PLAN_MODE_TOOLS_ENABLED", "1")

    # AskUserQuestion + EnterPlanMode are available in plan mode.
    plan_tools = _tool_names(
        build_cli_adk_tools(workspace_root=str(tmp_path), mode="plan")
    )
    assert {"AskUserQuestion", "EnterPlanMode"}.issubset(plan_tools)

    # ExitPlanMode is an act-mode tool per its manifest.
    act_tools = _tool_names(
        build_cli_adk_tools(workspace_root=str(tmp_path), mode="act")
    )
    assert "ExitPlanMode" in act_tools


def test_registry_handlers_bound_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_PLAN_MODE_TOOLS_ENABLED", "1")

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    for name in _PLAN_MODE_TOOLS:
        registration = runtime.registry.resolve_registration(name)
        assert registration is not None
        assert registration.handler is not None
        assert runtime.registry.is_enabled(name) is True
