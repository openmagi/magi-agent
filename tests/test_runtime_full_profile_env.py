from __future__ import annotations

from magi_agent.config.env import (
    apply_patch_enabled,
    browser_tool_enabled,
    file_tools_enabled,
    general_automation_live_enabled,
    is_evidence_ledger_lifecycle_enabled,
    is_format_on_write_enabled,
    is_message_cache_enabled,
    is_read_ledger_enabled,
    is_read_quality_enabled,
    is_self_introspection_enabled,
    model_aware_prompts_enabled,
    parse_lsp_diagnostics_env,
    parse_provider_repair_enabled,
    ripgrep_enabled,
    tool_concurrency_enabled,
)
from magi_agent.runtime.child_runner_live import is_live_child_runner_enabled
from magi_agent.runtime.child_toolset import resolve_child_toolset_profile
from magi_agent.runtime.local_defaults import apply_local_full_runtime_defaults


FULL_PROFILE_FLAGS = {
    "MAGI_READ_LEDGER_ENABLED": is_read_ledger_enabled,
    "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED": is_format_on_write_enabled,
    "MAGI_READ_QUALITY_ENABLED": is_read_quality_enabled,
    "MAGI_RIPGREP_ENABLED": ripgrep_enabled,
    "MAGI_APPLY_PATCH_ENABLED": apply_patch_enabled,
    "MAGI_PROVIDER_REPAIR_ENABLED": parse_provider_repair_enabled,
    "MAGI_TOOL_CONCURRENCY_ENABLED": tool_concurrency_enabled,
    "MAGI_MODEL_AWARE_PROMPTS_ENABLED": model_aware_prompts_enabled,
    "MAGI_GA_LIVE_ENABLED": general_automation_live_enabled,
    "MAGI_MESSAGE_CACHE_ENABLED": is_message_cache_enabled,
    "MAGI_FILE_TOOLS_ENABLED": file_tools_enabled,
    "MAGI_BROWSER_TOOL_ENABLED": browser_tool_enabled,
    "MAGI_SELF_INTROSPECTION_ENABLED": is_self_introspection_enabled,
    "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED": is_evidence_ledger_lifecycle_enabled,
}


def test_full_runtime_profile_enables_first_party_controls_by_default() -> None:
    for name, parser in FULL_PROFILE_FLAGS.items():
        assert parser({}) is True, name
    assert parse_lsp_diagnostics_env({}).enabled is True


def test_safe_runtime_profile_disables_first_party_controls_by_default() -> None:
    env = {"MAGI_RUNTIME_PROFILE": "safe"}
    for name, parser in FULL_PROFILE_FLAGS.items():
        assert parser(env) is False, name
    assert parse_lsp_diagnostics_env(env).enabled is False


def test_explicit_flag_off_overrides_full_runtime_profile() -> None:
    for name, parser in FULL_PROFILE_FLAGS.items():
        assert parser({name: "0"}) is False, name
    assert parse_lsp_diagnostics_env({"MAGI_LSP_DIAGNOSTICS_ENABLED": "0"}).enabled is False


def test_full_runtime_profile_enables_child_runner_defaults() -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)

    assert env["MAGI_CHILD_RUNNER_LIVE_ENABLED"] == "1"
    assert env["MAGI_CHILD_RUNNER_TOOLSET"] == "readonly"
    assert is_live_child_runner_enabled(env) is True
    assert resolve_child_toolset_profile(env) == "readonly"


def test_safe_runtime_profile_does_not_enable_child_runner_defaults() -> None:
    env = {"MAGI_RUNTIME_PROFILE": "safe"}
    apply_local_full_runtime_defaults(env)

    assert "MAGI_CHILD_RUNNER_LIVE_ENABLED" not in env
    assert is_live_child_runner_enabled(env) is False


def test_full_runtime_profile_enables_keyless_web_acquisition_defaults() -> None:
    # The local overlay should give a fresh, keyless user a working web
    # fetch/reader path (jina-reader is keyless; insane-fetch is local
    # curl_cffi). Reference the gate constants by name so the legacy-name
    # naming ratchet is not tripped by duplicated string literals.
    from magi_agent.web_acquisition.research_tools import (
        INSANE_FETCH_ENABLED_ENV,
        JINA_READER_ENABLED_ENV,
        LIVE_WEB_ACQUISITION_ENABLED_ENV,
        PROVIDER_ROUTER_ENABLED_ENV,
        build_native_web_boundary,
    )

    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)

    assert env[LIVE_WEB_ACQUISITION_ENABLED_ENV] == "1"
    assert env[PROVIDER_ROUTER_ENABLED_ENV] == "1"
    assert env[JINA_READER_ENABLED_ENV] == "1"
    assert env[INSANE_FETCH_ENABLED_ENV] == "1"
    # End-to-end: the native web boundary is actually reachable (non-None).
    assert build_native_web_boundary(env) is not None


def test_safe_runtime_profile_does_not_enable_web_acquisition() -> None:
    from magi_agent.web_acquisition.research_tools import (
        JINA_READER_ENABLED_ENV,
        build_native_web_boundary,
    )

    env = {"MAGI_RUNTIME_PROFILE": "safe"}
    apply_local_full_runtime_defaults(env)

    assert JINA_READER_ENABLED_ENV not in env
    assert build_native_web_boundary(env) is None


def test_browser_tool_kill_switch_overrides_full_runtime_default() -> None:
    assert browser_tool_enabled({"MAGI_BROWSER_TOOL_KILL_SWITCH": "1"}) is False
    assert (
        browser_tool_enabled(
            {
                "MAGI_BROWSER_TOOL_ENABLED": "1",
                "MAGI_BROWSER_TOOL_KILL_SWITCH": "on",
            }
        )
        is False
    )


# ---------------------------------------------------------------------------
# C4: key-aware model routes enabled in local overlay
# ---------------------------------------------------------------------------

def test_local_overlay_enables_key_aware_model_routes() -> None:
    """LOCAL_FULL_RUNTIME_ENV_DEFAULTS must include MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED=1."""
    from magi_agent.runtime.local_defaults import LOCAL_FULL_RUNTIME_ENV_DEFAULTS

    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS.get("MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED") == "1", (
        "MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED must be '1' in LOCAL_FULL_RUNTIME_ENV_DEFAULTS"
    )


def test_local_overlay_streams_thinking_with_reasoning_effort() -> None:
    """Local serve should show the collapsible thinking block by default: the
    model is asked to reason (MAGI_MODEL_REASONING_EFFORT) AND thought parts are
    forwarded on the thinking_delta channel (MAGI_STREAM_THINKING), rather than
    being dropped or merged into the body text."""
    from magi_agent.runtime.local_defaults import LOCAL_FULL_RUNTIME_ENV_DEFAULTS

    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS.get("MAGI_STREAM_THINKING") == "1"
    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS.get("MAGI_MODEL_REASONING_EFFORT") == "max"


def test_full_runtime_profile_applies_thinking_defaults() -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env.get("MAGI_STREAM_THINKING") == "1"
    assert env.get("MAGI_MODEL_REASONING_EFFORT") == "max"


def test_safe_runtime_profile_does_not_enable_thinking() -> None:
    env = {"MAGI_RUNTIME_PROFILE": "safe"}
    apply_local_full_runtime_defaults(env)
    assert "MAGI_STREAM_THINKING" not in env
    assert "MAGI_MODEL_REASONING_EFFORT" not in env
