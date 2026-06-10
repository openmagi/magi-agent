from __future__ import annotations

from magi_agent.cli.tool_runtime import build_cli_tool_runtime


def test_git_diff_handler_bound(tmp_path):
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration("GitDiff")
    assert registration is not None
    assert registration.handler is not None


def test_test_run_handler_bound(tmp_path):
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration("TestRun")
    assert registration is not None
    assert registration.handler is not None
