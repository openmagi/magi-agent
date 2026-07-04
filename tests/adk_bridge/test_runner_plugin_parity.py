"""Tests that real_runner and local_runner build plugins via the same shared helper.

Acceptance criteria:
- Both runners produce a ControlPlanePlugin (no plugins=[] divergence).
- The same env flags produce the same set of registered control names in both runners.
- real_runner no longer passes plugins=[].
"""

from __future__ import annotations

import os

import pytest

from magi_agent.adk_bridge.control_plane import (
    CONTROL_PLANE_PLUGIN_NAME,
    GA_CONSTRAINT_REINJECTION_CONTROL_NAME,
    ControlPlanePlugin,
)
from magi_agent.adk_bridge.local_runner import LocalInertLlm, LOCAL_INERT_MODEL_NAME


# Every env var that, when truthy in the *process* ``os.environ``, promotes a
# control into the plane. The core plane controls are ``flag_bool`` (profile-
# INDEPENDENT): the conservative ``safe`` runtime profile does NOT turn them off,
# so a value leaked into ``os.environ`` by a sibling test co-scheduled on the
# same xdist worker survives ``monkeypatch`` teardown (monkeypatch only restores
# keys the leaking test itself set) and populates the plane even under
# ``MAGI_RUNTIME_PROFILE=safe``, breaking ``test_both_runners_empty_plane_in_safe_profile``
# (observed: ``assert [<ResilienceLoopControl>, ...] == []``).
_PLANE_PROMOTING_ENV_VARS = (
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
    "MAGI_LOOP_GUARD_ENABLED",
    "MAGI_ERROR_RECOVERY_ENABLED",
    "MAGI_CONTEXT_COMPACTION_ENABLED",
    "MAGI_MAX_STEPS_BRAKE_ENABLED",
    "MAGI_SELF_REVIEW_ENABLED",
    "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED",
    "MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED",
    "MAGI_TOOL_NOT_FOUND_SOFT_FAIL_ENABLED",
    "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED",
    "MAGI_FACTS_REPLAN_ENABLED",
    "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED",
    "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED",
    "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED",
    "MAGI_GA_LIVE_ENABLED",
    "MAGI_PROVIDER_REPAIR_ENABLED",
)


@pytest.fixture(autouse=True)
def _hermetic_plane_env() -> "object":
    """Snapshot + restore ``os.environ`` and clear plane-promoting flags.

    Runs at setup (before each test body), so tests that intentionally set a
    promoting flag via ``monkeypatch.setenv`` (e.g. ``MAGI_EDIT_RETRY_REFLECTION_ENABLED=1``)
    still see their own value: the fixture clears leaked ambient state first, the
    test body then sets what it needs, and ``monkeypatch`` restores afterwards.
    This makes the ``safe`` profile's "empty plane" invariant observe the true
    safe-profile state regardless of which sibling file ran first on the worker.
    The safety assertion itself is NOT weakened.
    """
    saved = dict(os.environ)
    for key in _PLANE_PROMOTING_ENV_VARS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _inert_model_factory(_cfg):
    """A fake model factory that returns a proper ADK-compatible inert model."""
    return LocalInertLlm(model=LOCAL_INERT_MODEL_NAME)


# ---------------------------------------------------------------------------
# local_runner: ControlPlanePlugin is always present
# ---------------------------------------------------------------------------


def test_local_runner_has_control_plane_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    # All feature flags off (default state).
    for k in [
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
    ]:
        monkeypatch.delenv(k, raising=False)

    from magi_agent.adk_bridge import local_runner

    bundle = local_runner.build_local_adk_runner()
    plugin_names = {p.name for p in bundle.runner.plugin_manager.plugins}
    assert CONTROL_PLANE_PLUGIN_NAME in plugin_names


def test_local_runner_has_only_control_plane_plugin_when_flags_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    for k in [
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
    ]:
        monkeypatch.delenv(k, raising=False)

    from magi_agent.adk_bridge import local_runner

    bundle = local_runner.build_local_adk_runner()
    plugins = bundle.runner.plugin_manager.plugins
    # Exactly one plugin: the ControlPlanePlugin (with an empty plane when all flags off).
    assert len(plugins) == 1
    assert plugins[0].name == CONTROL_PLANE_PLUGIN_NAME


# ---------------------------------------------------------------------------
# real_runner: build_cli_model_runner wires the plane (no plugins=[])
# ---------------------------------------------------------------------------


def test_real_runner_has_control_plane_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """real_runner must not pass plugins=[] — it must wire a ControlPlanePlugin."""
    for k in [
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
    ]:
        monkeypatch.delenv(k, raising=False)

    from magi_agent.cli.providers import ProviderConfig
    from magi_agent.cli.real_runner import build_cli_model_runner

    cli_runner = build_cli_model_runner(
        ProviderConfig(
            provider="openai",
            model="gpt-4o",
            api_key="test-key",
        ),
        model_factory=_inert_model_factory,
        tools=[],
        instruction="test",
    )
    # The real runner wraps a google.adk.runners.Runner; we inspect its plugin_manager.
    runner = cli_runner._runner
    plugin_names = {p.name for p in runner.plugin_manager.plugins}
    assert CONTROL_PLANE_PLUGIN_NAME in plugin_names


# ---------------------------------------------------------------------------
# Parity: same env → same registered controls in both runners
# ---------------------------------------------------------------------------


def test_both_runners_register_same_controls_when_edit_retry_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With MAGI_EDIT_RETRY_REFLECTION_ENABLED=1, both runners have the same controls."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.setenv("MAGI_EDIT_RETRY_REFLECTION_ENABLED", "1")
    # Compare CORE control parity only: the customize after-tool control is a
    # real_runner-only extra (profile-aware default-ON), so pin it off here.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    for k in [
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
    ]:
        monkeypatch.delenv(k, raising=False)

    from magi_agent.adk_bridge import local_runner
    from magi_agent.cli.providers import ProviderConfig
    from magi_agent.cli.real_runner import build_cli_model_runner

    # Build both runners.
    bundle = local_runner.build_local_adk_runner()
    local_plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    local_control_names = {c.name for c in local_plane_plugin._p._controls}

    cli_runner = build_cli_model_runner(
        ProviderConfig(provider="openai", model="gpt-4o", api_key="x"),
        model_factory=_inert_model_factory,
        tools=[],
        instruction="test",
    )
    real_plane_plugin = next(
        p for p in cli_runner._runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    real_control_names = {c.name for c in real_plane_plugin._p._controls}

    # Both runners must have the same set of registered controls.
    assert local_control_names == real_control_names
    # edit_retry control must be present when flag is on.
    assert any("edit_retry" in name for name in real_control_names)


def test_both_runners_register_same_default_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the local full profile, both runners register the same default controls."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    # Compare CORE control parity only: the customize after-tool control is a
    # real_runner-only extra (profile-aware default-ON), so pin it off here.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    for k in [
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
        "MAGI_RUNTIME_PROFILE",
    ]:
        monkeypatch.delenv(k, raising=False)

    from magi_agent.adk_bridge import local_runner
    from magi_agent.cli.providers import ProviderConfig
    from magi_agent.cli.real_runner import build_cli_model_runner

    bundle = local_runner.build_local_adk_runner()
    local_plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )

    cli_runner = build_cli_model_runner(
        ProviderConfig(provider="openai", model="gpt-4o", api_key="x"),
        model_factory=_inert_model_factory,
        tools=[],
        instruction="test",
    )
    real_plane_plugin = next(
        p for p in cli_runner._runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )

    local_control_names = {c.name for c in local_plane_plugin._p._controls}
    real_control_names = {c.name for c in real_plane_plugin._p._controls}
    assert local_control_names == real_control_names
    assert any("edit_retry" in name for name in real_control_names)
    assert any("resilience" in name for name in real_control_names)
    assert any("compaction" in name for name in real_control_names)
    assert GA_CONSTRAINT_REINJECTION_CONTROL_NAME in real_control_names


def test_both_runners_empty_plane_in_safe_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conservative profile still leaves both planes empty."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")

    from magi_agent.adk_bridge import local_runner
    from magi_agent.cli.providers import ProviderConfig
    from magi_agent.cli.real_runner import build_cli_model_runner

    bundle = local_runner.build_local_adk_runner()
    local_plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )

    cli_runner = build_cli_model_runner(
        ProviderConfig(provider="openai", model="gpt-4o", api_key="x"),
        model_factory=_inert_model_factory,
        tools=[],
        instruction="test",
    )
    real_plane_plugin = next(
        p for p in cli_runner._runner.plugin_manager.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME
    )

    assert local_plane_plugin._p._controls == []
    assert real_plane_plugin._p._controls == []
