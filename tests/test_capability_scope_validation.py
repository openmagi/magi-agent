"""Tests for F4 — capability_scope custom-rule kind registration.

Covers ``validate_custom_rule`` accepting/rejecting ``capability_scope`` rules
once the kind is registered in ``custom_rules.KINDS`` / ``FIRES_AT`` / ``_LEGAL``.

The seed module ``magi_agent.customize.capability_scope`` already exposes
``validate_capability_scope_payload``; these tests exercise it indirectly via
the top-level ``validate_custom_rule`` dispatch.

Test plan:
  (a) valid denyTools-only payload                              → []
  (b) valid maxPermissionClass-only payload                     → []
  (c) tightenOnly: false                                        → error
  (d) tightenOnly missing                                       → error
  (e) widening attempts (unknown maxPermissionClass)            → error
  (f) firesAt=spawn + action=audit (legal matrix denies it)     → error
  (g) unknown maxPermissionClass                                → error
  (h) empty payload (no denyTools and no maxPermissionClass)    → error
  Plus: kind registered (sanity), firesAt=spawn slot exists, valid combined.
"""

from __future__ import annotations

from magi_agent.customize.custom_rules import (
    FIRES_AT,
    KINDS,
    validate_custom_rule,
)


def _cap(**over):
    """Minimal valid ``capability_scope`` rule for tweaking in tests."""
    rule = {
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "capability_scope",
            "payload": {
                "denyTools": ["Bash"],
                "tightenOnly": True,
            },
        },
        "firesAt": "spawn",
        "action": "block",
    }
    rule.update(over)
    return rule


# --- registration sanity ---
def test_capability_scope_kind_registered():
    assert "capability_scope" in KINDS


def test_spawn_fires_at_slot_registered():
    assert "spawn" in FIRES_AT


# --- (a) valid denyTools-only ---
def test_valid_deny_tools_only_payload():
    assert validate_custom_rule(_cap()) == []


# --- (b) valid maxPermissionClass-only ---
def test_valid_max_permission_class_only_payload():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {
                "maxPermissionClass": "readonly",
                "tightenOnly": True,
            },
        }
    )
    assert validate_custom_rule(rule) == []


def test_valid_combined_deny_and_cap_payload():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {
                "denyTools": ["Bash", "WebFetch"],
                "maxPermissionClass": "safe_write",
                "tightenOnly": True,
            },
        }
    )
    assert validate_custom_rule(rule) == []


# --- (c) tightenOnly: false rejected ---
def test_tighten_only_false_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {
                "denyTools": ["Bash"],
                "tightenOnly": False,
            },
        }
    )
    errors = validate_custom_rule(rule)
    assert errors, "tightenOnly=false must be rejected (no widening allowed)"
    assert any("tightenOnly" in e for e in errors)


# --- (d) tightenOnly missing rejected ---
def test_tighten_only_missing_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {"denyTools": ["Bash"]},
        }
    )
    errors = validate_custom_rule(rule)
    assert errors, "tightenOnly must be present (explicit declaration)"
    assert any("tightenOnly" in e for e in errors)


# --- (e) widening attempts (here: unknown class label as a widening proxy) ---
def test_widening_via_unknown_permission_class_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {
                "maxPermissionClass": "admin",  # not in {readonly, safe_write}
                "tightenOnly": True,
            },
        }
    )
    errors = validate_custom_rule(rule)
    assert errors
    assert any("maxPermissionClass" in e for e in errors)


# --- (f) firesAt=spawn + action=audit rejected by legal matrix ---
def test_capability_scope_spawn_audit_action_rejected():
    rule = _cap(action="audit")
    errors = validate_custom_rule(rule)
    assert errors, "capability_scope at spawn must reject action=audit"
    assert any("audit" in e or "action" in e for e in errors)


def test_capability_scope_wrong_fires_at_rejected():
    """capability_scope must only fire at the spawn slot."""
    rule = _cap(firesAt="pre_final")
    errors = validate_custom_rule(rule)
    assert errors
    assert any("pre_final" in e or "capability_scope" in e for e in errors)


# --- (g) unknown maxPermissionClass (explicit covers e above too) ---
def test_unknown_max_permission_class_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {
                "maxPermissionClass": "bogus",
                "tightenOnly": True,
            },
        }
    )
    errors = validate_custom_rule(rule)
    assert errors
    assert any("maxPermissionClass" in e for e in errors)


# --- (h) empty payload (no denyTools and no maxPermissionClass) ---
def test_empty_payload_no_deny_no_cap_rejected():
    """Rule must do *something* — declaring tightenOnly alone is inert."""
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {"tightenOnly": True},
        }
    )
    errors = validate_custom_rule(rule)
    assert errors
    assert any("denyTools" in e or "maxPermissionClass" in e for e in errors)


def test_empty_deny_list_with_no_cap_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {"denyTools": [], "tightenOnly": True},
        }
    )
    errors = validate_custom_rule(rule)
    assert errors


# --- extra: denyTools shape validation flows through ---
def test_deny_tools_non_string_entry_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {"denyTools": ["Bash", 7], "tightenOnly": True},
        }
    )
    errors = validate_custom_rule(rule)
    assert errors
    assert any("denyTools" in e for e in errors)


def test_deny_tools_duplicate_entry_rejected():
    rule = _cap(
        what={
            "kind": "capability_scope",
            "payload": {"denyTools": ["Bash", "Bash"], "tightenOnly": True},
        }
    )
    errors = validate_custom_rule(rule)
    assert errors
    assert any("duplicate" in e.lower() or "denyTools" in e for e in errors)
