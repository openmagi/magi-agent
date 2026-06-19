"""Tests for Task 2.1 — shacl_constraint custom-rule kind + validate_shape_ttl.

TDD: written before implementation — all tests must FAIL initially.
Spec: docs/plans/2026-06-18-shacl-PR2-customize-tasks.md Task 2.1

Test coverage:
  1. valid shacl_constraint rule → validate_custom_rule returns [].
  2. missing shapeTtl → error.
  3. malformed shapeTtl (broken Turtle) → error.
  4. shapeTtl containing sh:sparql → error.
  5. shapeTtl over 100 KB → error.
  6. shacl_constraint at firesAt=before_tool_use → _LEGAL violation error.
  7. validate_shape_ttl unit: valid→[], each bad case→non-empty, NONE raise.
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Minimal valid SHACL shape (reused from test_shacl_verifier.py style)
# ---------------------------------------------------------------------------

_VALID_SHAPE = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:AmountShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_amount ;
        sh:maxInclusive 3000 ;
        sh:message "amount must not exceed 3000" ;
    ] .
"""

_BROKEN_TURTLE = "this is not valid turtle @@@ !!! {"

_SPARQL_SHAPE = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .

magi:SparqlShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:sparql [
        sh:message "SPARQL constraint" ;
        sh:select \"\"\"SELECT ?this WHERE { ?this a magi:Evidence . }\"\"\" ;
    ] .
"""

_OVERSIZED_SHAPE = "x" * 100_001  # 100_001 bytes, exceeds _MAX_SHAPE_BYTES=100_000


def _valid_rule(**overrides) -> dict:
    """Build a minimal valid shacl_constraint rule."""
    base = {
        "id": "test-rule-1",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "shacl_constraint",
            "payload": {
                "shapeTtl": _VALID_SHAPE,
            },
        },
        "firesAt": "pre_final",
        "action": "block",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — valid shacl_constraint rule passes validation
# ---------------------------------------------------------------------------

def test_valid_shacl_constraint_rule_passes():
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule()
    errors = validate_custom_rule(rule)
    assert errors == [], f"Expected no errors, got: {errors}"


# ---------------------------------------------------------------------------
# Test 2 — missing shapeTtl → error
# ---------------------------------------------------------------------------

def test_missing_shape_ttl_returns_error():
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule()
    # Remove shapeTtl from payload
    rule["what"]["payload"] = {}
    errors = validate_custom_rule(rule)
    assert errors, "Expected errors when shapeTtl is missing"
    assert any("shapeTtl" in e for e in errors), (
        f"Expected 'shapeTtl' mentioned in errors, got: {errors}"
    )


# ---------------------------------------------------------------------------
# Test 3 — malformed shapeTtl (broken Turtle) → error
# ---------------------------------------------------------------------------

def test_malformed_shape_ttl_returns_error():
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule()
    rule["what"]["payload"]["shapeTtl"] = _BROKEN_TURTLE
    errors = validate_custom_rule(rule)
    assert errors, f"Expected errors for broken Turtle, got: {errors}"


# ---------------------------------------------------------------------------
# Test 4 — shapeTtl containing sh:sparql → error
# ---------------------------------------------------------------------------

def test_sparql_shape_ttl_returns_error():
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule()
    rule["what"]["payload"]["shapeTtl"] = _SPARQL_SHAPE
    errors = validate_custom_rule(rule)
    assert errors, f"Expected errors for sh:sparql shape, got: {errors}"


# ---------------------------------------------------------------------------
# Test 5 — shapeTtl over 100 KB → error
# ---------------------------------------------------------------------------

def test_oversized_shape_ttl_returns_error():
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule()
    rule["what"]["payload"]["shapeTtl"] = _OVERSIZED_SHAPE
    errors = validate_custom_rule(rule)
    assert errors, f"Expected errors for oversized shapeTtl, got: {errors}"


# ---------------------------------------------------------------------------
# Test 6 — shacl_constraint at before_tool_use → _LEGAL violation
# ---------------------------------------------------------------------------

def test_shacl_constraint_wrong_fires_at_returns_error():
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule(firesAt="before_tool_use")
    errors = validate_custom_rule(rule)
    assert errors, "Expected errors for shacl_constraint at before_tool_use"
    assert any("before_tool_use" in e or "shacl_constraint" in e for e in errors), (
        f"Expected _LEGAL violation error, got: {errors}"
    )


# ---------------------------------------------------------------------------
# Test 7 — validate_shape_ttl unit tests (NONE may raise)
# ---------------------------------------------------------------------------

class TestValidateShapeTtl:
    """Unit tests for validate_shape_ttl pure function."""

    def test_valid_shape_returns_empty_list(self):
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl(_VALID_SHAPE)
        assert result == [], f"Expected [], got: {result}"

    def test_empty_string_returns_errors(self):
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl("")
        assert result, "Expected errors for empty string"
        # Must not raise

    def test_oversized_shape_returns_errors(self):
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl(_OVERSIZED_SHAPE)
        assert result, "Expected errors for oversized shape"
        # Must not raise

    def test_malformed_turtle_returns_errors(self):
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl(_BROKEN_TURTLE)
        assert result, "Expected errors for malformed Turtle"
        # Must not raise

    def test_sparql_predicate_returns_errors(self):
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl(_SPARQL_SHAPE)
        assert result, "Expected errors for sh:sparql shape"
        # Must not raise

    def test_none_of_the_cases_raise(self):
        """Regression: validate_shape_ttl must be exception-safe in ALL cases."""
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        cases = [
            "",
            _OVERSIZED_SHAPE,
            _BROKEN_TURTLE,
            _SPARQL_SHAPE,
            _VALID_SHAPE,
        ]
        for case in cases:
            try:
                validate_shape_ttl(case)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"validate_shape_ttl raised unexpectedly for input "
                    f"{case[:40]!r}...: {exc}"
                )

    # F2 — non-str inputs must not raise; return a non-empty error list
    def test_none_input_returns_error_not_raise(self):
        """F2: validate_shape_ttl(None) must return a non-empty error list, never raise."""
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl(None)  # type: ignore[arg-type]
        assert result, "Expected errors for None input"
        assert not isinstance(result, Exception)

    def test_int_input_returns_error_not_raise(self):
        """F2: validate_shape_ttl(42) must return a non-empty error list, never raise."""
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl(42)  # type: ignore[arg-type]
        assert result, "Expected errors for int input"

    # F3 — whitespace-only shapeTtl must be treated as empty
    def test_whitespace_only_returns_error(self):
        """F3: whitespace-only shapeTtl must return a non-empty error (same as empty)."""
        from magi_agent.evidence.shacl_verifier import validate_shape_ttl

        result = validate_shape_ttl("   ")
        assert result, "Expected errors for whitespace-only shapeTtl"


# ---------------------------------------------------------------------------
# F1 — lazy import: validate_custom_rule works for non-shacl kinds without
#       loading shacl_verifier at module import time
# ---------------------------------------------------------------------------


def test_deterministic_ref_rule_validates_without_shacl_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """F1: validate_custom_rule for deterministic_ref must NOT import shacl_verifier.

    Asserts that validate_shape_ttl is NOT present at module scope in custom_rules
    (i.e. the lazy import was applied), then validates a deterministic_ref rule
    while blocking shacl_verifier in sys.modules — confirming no ImportError.
    """
    import sys
    import magi_agent.customize.custom_rules as cr

    # F1 core assertion: validate_shape_ttl must NOT be a module-level attribute
    # (it's now imported lazily inside the elif branch).
    assert not hasattr(cr, "validate_shape_ttl"), (
        "F1 FAIL: validate_shape_ttl is still a module-level name in custom_rules — "
        "the import must be lazy (inside the shacl_constraint branch only)"
    )

    # Additional robustness: block shacl_verifier in sys.modules and confirm
    # that a non-shacl rule still validates without ImportError.
    saved = sys.modules.pop("magi_agent.evidence.shacl_verifier", None)
    try:
        sys.modules["magi_agent.evidence.shacl_verifier"] = None  # type: ignore[assignment]

        rule = {
            "scope": "always",
            "what": {
                "kind": "deterministic_ref",
                "payload": {"ref": "no-code-execution"},
            },
            "firesAt": "pre_final",
            "action": "block",
        }
        # Must not raise even with shacl_verifier blocked
        errors = cr.validate_custom_rule(rule)
        # Errors about the ref value are fine; what matters is no ImportError.
        assert not any("ImportError" in e for e in errors)
    finally:
        # Restore sys.modules
        if saved is not None:
            sys.modules["magi_agent.evidence.shacl_verifier"] = saved
        else:
            sys.modules.pop("magi_agent.evidence.shacl_verifier", None)


# ---------------------------------------------------------------------------
# F4 — _LEGAL["shacl_constraint"] restricts to block-only
# ---------------------------------------------------------------------------


def test_shacl_constraint_audit_action_fails_validation() -> None:
    """F4: action='audit' for shacl_constraint must now fail validation (block-only)."""
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule(action="audit")
    errors = validate_custom_rule(rule)
    assert errors, "Expected errors for action='audit' on shacl_constraint (block-only)"
    assert any("audit" in e or "block" in e for e in errors), (
        f"Expected error mentioning 'audit' or 'block', got: {errors}"
    )


def test_shacl_constraint_retry_action_fails_validation() -> None:
    """F4: action='retry' for shacl_constraint must now fail validation (block-only)."""
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule(action="retry")
    errors = validate_custom_rule(rule)
    assert errors, "Expected errors for action='retry' on shacl_constraint (block-only)"
    assert any("retry" in e or "block" in e for e in errors), (
        f"Expected error mentioning 'retry' or 'block', got: {errors}"
    )


def test_shacl_constraint_block_action_still_valid() -> None:
    """F4 regression: action='block' must still be the one allowed action."""
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule(action="block")
    errors = validate_custom_rule(rule)
    assert errors == [], f"Expected no errors for action='block', got: {errors}"


# ---------------------------------------------------------------------------
# F3 (custom_rules level) — whitespace-only shapeTtl rejected by validate_custom_rule
# ---------------------------------------------------------------------------


def test_whitespace_only_shape_ttl_rejected_by_validate_custom_rule() -> None:
    """F3: whitespace-only shapeTtl must produce an error from validate_custom_rule."""
    from magi_agent.customize.custom_rules import validate_custom_rule

    rule = _valid_rule()
    rule["what"]["payload"]["shapeTtl"] = "   "
    errors = validate_custom_rule(rule)
    assert errors, "Expected errors for whitespace-only shapeTtl"
    assert any("shapeTtl" in e for e in errors), (
        f"Expected 'shapeTtl' mentioned in errors, got: {errors}"
    )
