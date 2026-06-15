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
