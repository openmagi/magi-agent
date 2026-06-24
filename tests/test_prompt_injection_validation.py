"""F-MUT1 — validator + apply contract tests for the prompt_injection kind."""

from __future__ import annotations

from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.prompt_injection import (
    VALUE_MAX,
    apply_prompt_injection_to_prompt_sections,
    apply_prompt_injection_to_tool_args,
    validate_prompt_injection_payload,
)


# ---------------------------------------------------------------------------
# Direct validator — before_tool_use shape
# ---------------------------------------------------------------------------


def test_valid_before_tool_use_payload() -> None:
    payload = {
        "mode": "append",
        "target_arg_key": "command",
        "value": " --dry-run",
    }
    assert validate_prompt_injection_payload(payload, "before_tool_use") == []


def test_before_tool_use_rejects_missing_target_arg_key() -> None:
    payload = {"mode": "append", "value": " --dry-run"}
    errors = validate_prompt_injection_payload(payload, "before_tool_use")
    assert any("target_arg_key" in e for e in errors)


def test_before_tool_use_rejects_target_field() -> None:
    payload = {
        "mode": "append",
        "target_arg_key": "command",
        "target": "system_prompt",
        "value": "x",
    }
    errors = validate_prompt_injection_payload(payload, "before_tool_use")
    assert any("target" in e and "only valid" in e for e in errors)


def test_before_tool_use_accepts_optional_condition() -> None:
    payload = {
        "mode": "append",
        "target_arg_key": "command",
        "value": " --dry-run",
        "condition": {"tool": "shell_exec", "regex": "^rm"},
    }
    assert validate_prompt_injection_payload(payload, "before_tool_use") == []


def test_before_tool_use_rejects_invalid_regex_in_condition() -> None:
    payload = {
        "mode": "append",
        "target_arg_key": "command",
        "value": " --dry-run",
        "condition": {"regex": "[unterminated"},
    }
    errors = validate_prompt_injection_payload(payload, "before_tool_use")
    assert any("not a valid regex" in e for e in errors)


# ---------------------------------------------------------------------------
# Direct validator — on_user_prompt_submit shape
# ---------------------------------------------------------------------------


def test_valid_on_user_prompt_submit_payload() -> None:
    payload = {
        "mode": "append",
        "target": "system_prompt",
        "value": "Always cite sources.",
    }
    assert validate_prompt_injection_payload(payload, "on_user_prompt_submit") == []


def test_on_user_prompt_submit_rejects_target_arg_key() -> None:
    payload = {
        "mode": "append",
        "target": "system_prompt",
        "target_arg_key": "command",
        "value": "x",
    }
    errors = validate_prompt_injection_payload(payload, "on_user_prompt_submit")
    assert any("target_arg_key" in e and "only valid" in e for e in errors)


def test_on_user_prompt_submit_rejects_unknown_target() -> None:
    payload = {"mode": "append", "target": "tool_args", "value": "x"}
    errors = validate_prompt_injection_payload(payload, "on_user_prompt_submit")
    assert any("system_prompt" in e for e in errors)


# ---------------------------------------------------------------------------
# Mode constraints
# ---------------------------------------------------------------------------


def test_rejects_mode_replace_with_v2_pointer() -> None:
    payload = {
        "mode": "replace",
        "target": "system_prompt",
        "value": "x",
    }
    errors = validate_prompt_injection_payload(payload, "on_user_prompt_submit")
    assert any("v2" in e for e in errors)


def test_rejects_unknown_mode() -> None:
    payload = {
        "mode": "prepend",
        "target": "system_prompt",
        "value": "x",
    }
    errors = validate_prompt_injection_payload(payload, "on_user_prompt_submit")
    assert any("must be 'append'" in e for e in errors)


# ---------------------------------------------------------------------------
# Value bounds + lifecycle slot guard
# ---------------------------------------------------------------------------


def test_rejects_value_over_cap() -> None:
    payload = {
        "mode": "append",
        "target": "system_prompt",
        "value": "x" * (VALUE_MAX + 1),
    }
    errors = validate_prompt_injection_payload(payload, "on_user_prompt_submit")
    assert any(f"{VALUE_MAX}-char cap" in e for e in errors)


def test_rejects_unknown_fires_at() -> None:
    payload = {"mode": "append", "value": "x", "target": "system_prompt"}
    errors = validate_prompt_injection_payload(payload, "pre_final")
    assert any("only fire" in e for e in errors)


# ---------------------------------------------------------------------------
# End-to-end through validate_custom_rule (the PUT endpoint contract)
# ---------------------------------------------------------------------------


def test_custom_rule_before_tool_use_prompt_injection_valid() -> None:
    rule = {
        "id": "cr_fmut1_dry_run",
        "scope": "coding",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "command",
                "value": " --dry-run",
            },
        },
        "firesAt": "before_tool_use",
        "action": "audit",
    }
    assert validate_custom_rule(rule) == []


def test_custom_rule_on_user_prompt_submit_prompt_injection_valid() -> None:
    rule = {
        "id": "cr_fmut1_standards",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target": "system_prompt",
                "value": "Follow our coding standards.",
            },
        },
        "firesAt": "on_user_prompt_submit",
        "action": "audit",
    }
    assert validate_custom_rule(rule) == []


def test_custom_rule_pre_final_prompt_injection_rejected() -> None:
    """``prompt_injection`` is only legal at the two F-MUT1 slots."""
    rule = {
        "id": "cr_bad",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {"mode": "append", "value": "x"},
        },
        "firesAt": "pre_final",
        "action": "audit",
    }
    errors = validate_custom_rule(rule)
    assert any("cannot fire at" in e for e in errors)


def test_custom_rule_block_action_rejected_for_prompt_injection() -> None:
    rule = {
        "id": "cr_bad_action",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target": "system_prompt",
                "value": "x",
            },
        },
        "firesAt": "on_user_prompt_submit",
        "action": "block",
    }
    errors = validate_custom_rule(rule)
    assert any("allows actions" in e and "'block'" in e for e in errors)


# ---------------------------------------------------------------------------
# apply_prompt_injection_to_tool_args
# ---------------------------------------------------------------------------


def _before_rule(**over) -> dict:
    rule = {
        "id": "cr_dry_run",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "command",
                "value": " --dry-run",
            },
        },
        "firesAt": "before_tool_use",
        "action": "audit",
    }
    rule.update(over)
    return rule


def test_apply_tool_args_appends_value_to_target_key() -> None:
    out = apply_prompt_injection_to_tool_args(
        {"command": "ls"}, [_before_rule()], "shell_exec"
    )
    assert out == {"command": "ls --dry-run"}


def test_apply_tool_args_preserves_other_keys() -> None:
    out = apply_prompt_injection_to_tool_args(
        {"command": "ls", "cwd": "/tmp"}, [_before_rule()], "shell_exec"
    )
    assert out == {"command": "ls --dry-run", "cwd": "/tmp"}


def test_apply_tool_args_no_mutation_when_disabled() -> None:
    rule = _before_rule(enabled=False)
    out = apply_prompt_injection_to_tool_args(
        {"command": "ls"}, [rule], "shell_exec"
    )
    assert out == {"command": "ls"}


def test_apply_tool_args_skips_rule_for_other_tool() -> None:
    rule = _before_rule()
    rule["what"]["payload"]["condition"] = {"tool": "bash"}
    out = apply_prompt_injection_to_tool_args(
        {"command": "ls"}, [rule], "shell_exec"
    )
    assert out == {"command": "ls"}


def test_apply_tool_args_regex_condition_match_only() -> None:
    rule = _before_rule()
    rule["what"]["payload"]["condition"] = {"regex": "^rm"}
    # Does not match
    out = apply_prompt_injection_to_tool_args(
        {"command": "ls"}, [rule], "shell_exec"
    )
    assert out == {"command": "ls"}
    # Matches
    out = apply_prompt_injection_to_tool_args(
        {"command": "rm -rf"}, [rule], "shell_exec"
    )
    assert out == {"command": "rm -rf --dry-run"}


def test_apply_tool_args_drops_malformed_rule_silently() -> None:
    bad = {
        "id": "cr_bad",
        "enabled": True,
        "what": {"kind": "prompt_injection", "payload": None},
        "firesAt": "before_tool_use",
        "action": "audit",
    }
    out = apply_prompt_injection_to_tool_args(
        {"command": "ls"}, [bad, _before_rule()], "shell_exec"
    )
    # the good rule still fires
    assert out == {"command": "ls --dry-run"}


def test_apply_tool_args_input_is_not_mutated() -> None:
    args = {"command": "ls"}
    apply_prompt_injection_to_tool_args(args, [_before_rule()], "shell_exec")
    assert args == {"command": "ls"}


# ---------------------------------------------------------------------------
# apply_prompt_injection_to_prompt_sections
# ---------------------------------------------------------------------------


def _system_rule(**over) -> dict:
    rule = {
        "id": "cr_standards",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target": "system_prompt",
                "value": "Always cite sources.",
            },
        },
        "firesAt": "on_user_prompt_submit",
        "action": "audit",
    }
    rule.update(over)
    return rule


def test_apply_sections_appends_value_as_new_section() -> None:
    out = apply_prompt_injection_to_prompt_sections(
        ["base"], [_system_rule()]
    )
    assert out == ["base", "Always cite sources."]


def test_apply_sections_no_mutation_when_disabled() -> None:
    rule = _system_rule(enabled=False)
    out = apply_prompt_injection_to_prompt_sections(["base"], [rule])
    assert out == ["base"]


def test_apply_sections_preserves_existing_section_order() -> None:
    out = apply_prompt_injection_to_prompt_sections(
        ["a", "b", "c"], [_system_rule()]
    )
    assert out == ["a", "b", "c", "Always cite sources."]


def test_apply_sections_input_is_not_mutated() -> None:
    sections = ["base"]
    apply_prompt_injection_to_prompt_sections(sections, [_system_rule()])
    assert sections == ["base"]


def test_apply_sections_drops_rule_without_system_prompt_target() -> None:
    rule = _system_rule()
    rule["what"]["payload"]["target"] = "elsewhere"
    out = apply_prompt_injection_to_prompt_sections(["base"], [rule])
    assert out == ["base"]


def test_apply_sections_composes_multiple_rules_in_order() -> None:
    rule_a = _system_rule(id="a")
    rule_b = _system_rule(id="b")
    rule_b["what"]["payload"]["value"] = "Prefer tests."
    out = apply_prompt_injection_to_prompt_sections(
        ["base"], [rule_a, rule_b]
    )
    assert out == ["base", "Always cite sources.", "Prefer tests."]
