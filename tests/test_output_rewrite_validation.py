"""F-MUT2 — validator + apply contract tests for the output_rewrite kind."""

from __future__ import annotations

from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.output_rewrite import (
    PATTERN_MAX,
    REPLACEMENT_MAX,
    apply_output_rewrite_to_tool_result,
    validate_output_rewrite_payload,
)
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Direct validator — after_tool_use shape (the only legal slot in v1)
# ---------------------------------------------------------------------------


def test_redact_valid() -> None:
    payload = {
        "mode": "redact",
        "pattern": "AKIA[0-9A-Z]{16}",
        "replacement": "***",
    }
    assert validate_output_rewrite_payload(payload, "after_tool_use") == []


def test_redact_valid_with_optional_fields() -> None:
    payload = {
        "mode": "redact",
        "pattern": "secret",
        "replacement": "[redacted]",
        "scope": "full_output",
        "isRegex": False,
        "toolMatch": {"include": ["shell_exec"], "exclude": ["read"]},
    }
    assert validate_output_rewrite_payload(payload, "after_tool_use") == []


# ---------------------------------------------------------------------------
# Mode constraints
# ---------------------------------------------------------------------------


def test_rejects_mode_summarize() -> None:
    payload = {
        "mode": "summarize",
        "pattern": "x",
        "replacement": "y",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("v2" in e and "admin" in e for e in errors)


def test_rejects_mode_replace() -> None:
    payload = {
        "mode": "replace",
        "pattern": "x",
        "replacement": "y",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("v2" in e and "admin" in e for e in errors)


def test_rejects_unknown_mode() -> None:
    payload = {
        "mode": "rewrite-everything",
        "pattern": "x",
        "replacement": "y",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("must be 'redact'" in e for e in errors)


# ---------------------------------------------------------------------------
# Pattern / replacement / scope bounds
# ---------------------------------------------------------------------------


def test_rejects_malformed_regex() -> None:
    payload = {
        "mode": "redact",
        "pattern": "[unterminated",
        "replacement": "***",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("not a valid regex" in e for e in errors)


def test_rejects_empty_pattern() -> None:
    payload = {
        "mode": "redact",
        "pattern": "",
        "replacement": "***",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("pattern is required" in e for e in errors)


def test_rejects_pattern_too_long() -> None:
    payload = {
        "mode": "redact",
        "pattern": "x" * (PATTERN_MAX + 1),
        "replacement": "***",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any(f"{PATTERN_MAX}-char cap" in e for e in errors)


def test_rejects_replacement_too_long() -> None:
    payload = {
        "mode": "redact",
        "pattern": "x",
        "replacement": "y" * (REPLACEMENT_MAX + 1),
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any(f"{REPLACEMENT_MAX}-char cap" in e for e in errors)


def test_rejects_invalid_scope() -> None:
    payload = {
        "mode": "redact",
        "pattern": "x",
        "replacement": "y",
        "scope": "everywhere",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("scope" in e and "match_only" in e for e in errors)


def test_rejects_non_bool_is_regex() -> None:
    payload = {
        "mode": "redact",
        "pattern": "x",
        "replacement": "y",
        "isRegex": "yes",
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("isRegex" in e and "boolean" in e for e in errors)


def test_rejects_bad_tool_match_shape() -> None:
    payload = {
        "mode": "redact",
        "pattern": "x",
        "replacement": "y",
        "toolMatch": {"include": ["", "x"]},
    }
    errors = validate_output_rewrite_payload(payload, "after_tool_use")
    assert any("toolMatch.include" in e for e in errors)


# ---------------------------------------------------------------------------
# Lifecycle slot guard
# ---------------------------------------------------------------------------


def test_rejects_wrong_lifecycle_slot() -> None:
    payload = {
        "mode": "redact",
        "pattern": "x",
        "replacement": "y",
    }
    errors = validate_output_rewrite_payload(payload, "pre_final")
    assert any("only fire" in e and "after_tool_use" in e for e in errors)


# ---------------------------------------------------------------------------
# End-to-end through validate_custom_rule (the PUT endpoint contract)
# ---------------------------------------------------------------------------


def test_custom_rule_after_tool_use_output_rewrite_valid() -> None:
    rule = {
        "id": "cr_fmut2_redact_aws_key",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "AKIA[0-9A-Z]{16}",
                "replacement": "***",
            },
        },
        "firesAt": "after_tool_use",
        "action": "audit",
    }
    assert validate_custom_rule(rule) == []


def test_custom_rule_pre_final_output_rewrite_rejected() -> None:
    """``output_rewrite`` is only legal at after_tool_use."""
    rule = {
        "id": "cr_bad",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "x",
                "replacement": "y",
            },
        },
        "firesAt": "pre_final",
        "action": "audit",
    }
    errors = validate_custom_rule(rule)
    assert any("cannot fire at" in e for e in errors)


def test_custom_rule_block_action_rejected_for_output_rewrite() -> None:
    rule = {
        "id": "cr_bad_action",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "x",
                "replacement": "y",
            },
        },
        "firesAt": "after_tool_use",
        "action": "block",
    }
    errors = validate_custom_rule(rule)
    assert any("allows actions" in e and "'block'" in e for e in errors)


# ---------------------------------------------------------------------------
# apply_output_rewrite_to_tool_result — pure helper contract
# ---------------------------------------------------------------------------


def _redact_rule(**over) -> dict:
    rule = {
        "id": "cr_redact",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "AKIA[0-9A-Z]{16}",
                "replacement": "***",
            },
        },
        "firesAt": "after_tool_use",
        "action": "audit",
    }
    rule.update(over)
    return rule


def test_apply_rewrites_matching_pattern() -> None:
    result = ToolResult(status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world")
    new = apply_output_rewrite_to_tool_result(result, [_redact_rule()], "shell_exec")
    assert new.output == "hello *** world"
    # Status and other fields preserved.
    assert new.status == "ok"


def test_apply_returns_original_when_no_match() -> None:
    result = ToolResult(status="ok", output="nothing sensitive here")
    new = apply_output_rewrite_to_tool_result(result, [_redact_rule()], "shell_exec")
    assert new is result


def test_apply_no_mutation_when_disabled() -> None:
    rule = _redact_rule(enabled=False)
    result = ToolResult(status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world")
    new = apply_output_rewrite_to_tool_result(result, [rule], "shell_exec")
    assert new is result


def test_apply_skips_non_string_output() -> None:
    result = ToolResult(status="ok", output={"key": "AKIABCDEFGHIJKLMNOPQ"})
    new = apply_output_rewrite_to_tool_result(result, [_redact_rule()], "shell_exec")
    assert new is result


def test_apply_tool_match_include_filter() -> None:
    rule = _redact_rule()
    rule["what"]["payload"]["toolMatch"] = {"include": ["read_file"]}
    result = ToolResult(status="ok", output="key AKIABCDEFGHIJKLMNOPQ")
    # Tool not in include list → no rewrite.
    new = apply_output_rewrite_to_tool_result(result, [rule], "shell_exec")
    assert new is result
    # Tool in include list → rewrite.
    new2 = apply_output_rewrite_to_tool_result(result, [rule], "read_file")
    assert new2.output == "key ***"


def test_apply_tool_match_exclude_filter() -> None:
    rule = _redact_rule()
    rule["what"]["payload"]["toolMatch"] = {"exclude": ["shell_exec"]}
    result = ToolResult(status="ok", output="key AKIABCDEFGHIJKLMNOPQ")
    new = apply_output_rewrite_to_tool_result(result, [rule], "shell_exec")
    assert new is result


def test_apply_literal_mode_via_is_regex_false() -> None:
    rule = _redact_rule()
    rule["what"]["payload"]["pattern"] = "secret.value"
    rule["what"]["payload"]["replacement"] = "X"
    rule["what"]["payload"]["isRegex"] = False
    # The literal pattern contains a regex special char (`.`); literal mode
    # must escape it so a string "secretXvalue" is NOT matched.
    result = ToolResult(status="ok", output="payload secret.value end")
    new = apply_output_rewrite_to_tool_result(result, [rule], "shell_exec")
    assert new.output == "payload X end"


def test_apply_composes_multiple_rules_in_order() -> None:
    rule_a = _redact_rule(id="a")
    rule_b = _redact_rule(id="b")
    rule_b["what"]["payload"]["pattern"] = "hello"
    rule_b["what"]["payload"]["replacement"] = "HI"
    result = ToolResult(status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world")
    new = apply_output_rewrite_to_tool_result(
        result, [rule_a, rule_b], "shell_exec"
    )
    assert new.output == "HI *** world"


def test_apply_drops_malformed_rule_silently() -> None:
    bad = {
        "id": "cr_bad",
        "enabled": True,
        "what": {"kind": "output_rewrite", "payload": None},
        "firesAt": "after_tool_use",
        "action": "audit",
    }
    result = ToolResult(status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world")
    new = apply_output_rewrite_to_tool_result(
        result, [bad, _redact_rule()], "shell_exec"
    )
    # the good rule still fires
    assert new.output == "hello *** world"


def test_apply_input_is_not_mutated() -> None:
    rule = _redact_rule()
    result = ToolResult(status="ok", output="hello AKIABCDEFGHIJKLMNOPQ world")
    apply_output_rewrite_to_tool_result(result, [rule], "shell_exec")
    # Original instance still has the sensitive text.
    assert result.output == "hello AKIABCDEFGHIJKLMNOPQ world"
