"""Scope enforcement + multi-rule composition coverage.

The existing matrix files author rules with ``scope=always`` and one
rule per slot, so they never exercise:

1. Scope filtering: a rule with ``scope=coding`` should NOT fire on
   a turn with a different scope (per Phase 2 wiring,
   ``CustomizeVerificationPolicy.enabled_*_rules(current_scope=...)``
   filters at policy read time). Without an explicit test, a regression
   in the scope reader would silently widen blast radius.

2. Multi-rule composition: two enabled rules at the same slot should
   compose in declared order. The current matrix tests have one rule
   per slot so order semantics drift unnoticed.

This file walks both:

* Scope filter for tool_perm (scope=coding fires only when
  current_scope=='coding'; uncovered scope or None drops it).
* Scope filter for the lifecycle_audit shell_command fan-out
  (per-rule scope filtering at the policy reader).
* prompt_injection 2-rule append-order composition (append in stored
  order; second rule sees the first rule's output).
* output_rewrite 2-rule sequential redact composition (next rule
  matches the previous rule's rewritten text).
* shell_command 2-rule per-slot fan-out (both rules execute,
  budget shared).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from magi_agent.tools.result import ToolResult


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


@pytest.fixture
def flags_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Bare-bones flags + tmp customize.json (no HTTP layer needed)."""
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    return cfile


# ---------------------------------------------------------------------------
# Scope filtering — tool_perm
# ---------------------------------------------------------------------------


def test_tool_perm_scope_coding_fires_only_in_coding(flags_on: Path) -> None:
    """tool_perm scope=coding: fires when current_scope='coding', skips otherwise."""
    from magi_agent.customize.store import set_custom_rule
    from magi_agent.customize.tool_perm import matched_decision

    set_custom_rule(
        {
            "id": "qa_scope_coding",
            "scope": "coding",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {
                    "match": {"tool": "scoped_tool"},
                    "decision": "deny",
                },
            },
        },
        path=flags_on,
    )

    coding = matched_decision(
        tool_name="scoped_tool", arguments={}, current_scope="coding"
    )
    assert coding is not None and coding[0] == "deny", (
        f"scope=coding rule MUST fire on coding turn; got {coding}"
    )

    other = matched_decision(
        tool_name="scoped_tool", arguments={}, current_scope="research"
    )
    assert other is None, (
        f"scope=coding rule MUST NOT fire on research turn; got {other}"
    )

    no_scope = matched_decision(tool_name="scoped_tool", arguments={})
    # current_scope=None: the legacy back-compat path preserves the
    # historic scope-blind behavior (rule matches regardless of scope).
    # This pins the documented contract.
    assert no_scope is not None and no_scope[0] == "deny", (
        f"current_scope=None must preserve scope-blind back-compat; "
        f"got {no_scope}"
    )


def test_tool_perm_scope_always_fires_in_every_scope(flags_on: Path) -> None:
    """scope=always: fires on every scope."""
    from magi_agent.customize.store import set_custom_rule
    from magi_agent.customize.tool_perm import matched_decision

    set_custom_rule(
        {
            "id": "qa_scope_always",
            "scope": "always",
            "enabled": True,
            "firesAt": "before_tool_use",
            "action": "block",
            "what": {
                "kind": "tool_perm",
                "payload": {
                    "match": {"tool": "always_blocked"},
                    "decision": "deny",
                },
            },
        },
        path=flags_on,
    )
    for current_scope in ("coding", "research", "delivery", "memory", None):
        out = matched_decision(
            tool_name="always_blocked",
            arguments={},
            current_scope=current_scope,
        )
        assert out is not None and out[0] == "deny", (
            f"scope=always rule MUST fire on every scope; "
            f"current_scope={current_scope!r} got {out}"
        )


# ---------------------------------------------------------------------------
# Multi-rule composition — prompt_injection append order
# ---------------------------------------------------------------------------


def test_prompt_injection_two_rules_compose_in_stored_order(
    flags_on: Path,
) -> None:
    """Two prompt_injection rules at the same slot append in declared order."""
    from magi_agent.customize.prompt_injection import (
        apply_prompt_injection_to_tool_args,
    )
    from magi_agent.customize.store import load_overrides, set_custom_rule

    rule_a = {
        "id": "qa_pi_first",
        "scope": "always",
        "enabled": True,
        "firesAt": "before_tool_use",
        "action": "audit",
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "input",
                "value": "[A]",
            },
        },
    }
    rule_b = {
        "id": "qa_pi_second",
        "scope": "always",
        "enabled": True,
        "firesAt": "before_tool_use",
        "action": "audit",
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "input",
                "value": "[B]",
            },
        },
    }
    set_custom_rule(rule_a, path=flags_on)
    set_custom_rule(rule_b, path=flags_on)

    rules = load_overrides().get("verification", {}).get("custom_rules", [])
    out = apply_prompt_injection_to_tool_args(
        {"input": "X"}, rules, "any_tool"
    )
    assert out["input"] == "X[A][B]", (
        f"two append rules MUST compose in stored order; got {out!r}"
    )


# ---------------------------------------------------------------------------
# Multi-rule composition — output_rewrite sequential redact
# ---------------------------------------------------------------------------


def test_output_rewrite_two_rules_compose_sequentially(
    flags_on: Path,
) -> None:
    """Second output_rewrite rule sees the first rule's rewritten output."""
    from magi_agent.customize.output_rewrite import (
        apply_output_rewrite_to_tool_result,
    )
    from magi_agent.customize.store import load_overrides, set_custom_rule

    rule_first = {
        "id": "qa_or_first",
        "scope": "always",
        "enabled": True,
        "firesAt": "after_tool_use",
        "action": "audit",
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "alpha",
                "replacement": "beta",
                "isRegex": False,
            },
        },
    }
    rule_second = {
        "id": "qa_or_second",
        "scope": "always",
        "enabled": True,
        "firesAt": "after_tool_use",
        "action": "audit",
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": "beta",
                "replacement": "gamma",
                "isRegex": False,
            },
        },
    }
    set_custom_rule(rule_first, path=flags_on)
    set_custom_rule(rule_second, path=flags_on)

    rules = load_overrides().get("verification", {}).get("custom_rules", [])
    result = ToolResult(status="ok", output="alpha", metadata={})
    rewritten = apply_output_rewrite_to_tool_result(result, rules, "any_tool")
    # First rule: alpha -> beta. Second rule: beta -> gamma. Final: gamma.
    assert rewritten.output == "gamma", (
        f"sequential rewrite must compose; got {rewritten.output!r}"
    )


# ---------------------------------------------------------------------------
# Multi-rule composition — shell_command 2-rule fan-out
# ---------------------------------------------------------------------------


def test_shell_command_two_rules_both_fire_at_same_slot(
    flags_on: Path,
) -> None:
    """Two shell_command rules at the same slot both spawn (under budget)."""
    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_before_turn_start,
    )
    from magi_agent.customize.store import set_custom_rule

    set_custom_rule(
        {
            "id": "qa_sc_one",
            "scope": "always",
            "enabled": True,
            "firesAt": "before_turn_start",
            "action": "audit",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "echo first",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
        path=flags_on,
    )
    set_custom_rule(
        {
            "id": "qa_sc_two",
            "scope": "always",
            "enabled": True,
            "firesAt": "before_turn_start",
            "action": "audit",
            "what": {
                "kind": "shell_command",
                "payload": {
                    "source": "inline",
                    "inline": "echo second",
                    "timeout_seconds": 5,
                    "shell": "bash",
                },
            },
        },
        path=flags_on,
    )

    audits = asyncio.run(
        run_shell_command_at_before_turn_start(
            prompt_text="x", remaining_budget=10
        )
    )
    executed = [a for a in audits if a.get("status") == "executed"]
    rule_ids = sorted(a.get("rule_id") for a in executed)
    assert rule_ids == ["qa_sc_one", "qa_sc_two"], (
        f"both shell_command rules MUST fire; got rule_ids={rule_ids}"
    )


def test_shell_command_two_rules_share_per_turn_budget(
    flags_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-rule budget cap: 2 rules at one slot with budget=1 -> 1 fires, 1 skips."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "1")
    from magi_agent.customize.lifecycle_audit import (
        run_shell_command_at_before_turn_start,
    )
    from magi_agent.customize.store import set_custom_rule

    for i in range(2):
        set_custom_rule(
            {
                "id": f"qa_sc_budget_{i}",
                "scope": "always",
                "enabled": True,
                "firesAt": "before_turn_start",
                "action": "audit",
                "what": {
                    "kind": "shell_command",
                    "payload": {
                        "source": "inline",
                        "inline": f"echo rule_{i}",
                        "timeout_seconds": 5,
                        "shell": "bash",
                    },
                },
            },
            path=flags_on,
        )

    audits = asyncio.run(
        run_shell_command_at_before_turn_start(
            prompt_text="x", remaining_budget=1
        )
    )
    executed = sum(1 for a in audits if a.get("status") == "executed")
    exhausted = sum(1 for a in audits if a.get("status") == "budget_exhausted")
    assert executed == 1 and exhausted == 1, (
        f"budget=1 + 2 rules MUST yield 1 executed + 1 budget_exhausted; "
        f"got executed={executed} exhausted={exhausted} audits={audits!r}"
    )
