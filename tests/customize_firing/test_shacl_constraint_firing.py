"""F1 firing test — prove a shacl_constraint rule fires when a shape does NOT conform.

Goal
----
End-to-end smoke at the runtime-entry layer:

  1.  Build a tmp ``customize.json`` containing a ``shacl_constraint`` custom rule
      whose payload is a SHACL shape requiring ``TestRun.exitCode == 0``.
  2.  Flip the three runtime flags ON via ``monkeypatch.setenv``::

          MAGI_CUSTOMIZE_VERIFICATION_ENABLED=1
          MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED=1
          MAGI_SHACL_VERIFIER_ENABLED=1

  3.  Synthesise an :class:`EvidenceRecord` of type ``TestRun`` with
      ``exitCode=1`` (non-conform).
  4.  Invoke ``magi_agent.evidence.shacl_verifier.run_shacl_rule`` with the
      record list, the rule's ``shapeTtl`` payload, and a fixed
      ``observed_at``.
  5.  Assert validation FAILS (``status='failed'`` / ``conforms=False`` /
      at least one violation).
  6.  Repeat with ``exitCode=0`` and assert validation PASSES
      (``status='ok'`` / ``conforms=True`` / no violations).

Why hit ``run_shacl_rule`` directly rather than the contract-evaluator stack?
The task statement is explicit: "Call magi_agent.evidence.shacl_verifier.
run_shacl_rule (or runtime entry -- grep)".  ``run_shacl_rule`` IS the runtime
entry point for the verifier kernel: the custom-rule evaluator parses the rule
payload, surfaces the ``shapeTtl``, and forwards it to this pure function.
Driving it directly gives a deterministic firing test without dragging in the
full contract-bus seam.

Hard-skip when optional deps absent
-----------------------------------
``rdflib`` and ``pyshacl`` are optional dev/cli extras.  ``pytest.importorskip``
at module top makes the whole file skip cleanly on minimal envs (mirroring
``tests/test_anthropic_cache_model.py`` and acceptable per the task brief).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Optional heavy deps — skip the entire module if either is missing.
# This mirrors the pattern used in tests/test_anthropic_cache_model.py where
# the optional vendor SDK is gated at the call-site; here we gate at module
# scope because every test in this file needs both libraries.
pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")

from magi_agent.evidence.shacl_verifier import run_shacl_rule  # noqa: E402
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

# Deterministic observed_at — the verifier injects this verbatim into the
# emitted EvidenceRecord, so a fixed value keeps the test fully reproducible.
_OBSERVED_AT = 1_718_000_000

# SHACL shape: every magi:Evidence node whose magi:type literal is "TestRun"
# MUST carry exactly one magi:field_exitCode whose value equals 0.
#
# Targeting trick: ``sh:targetClass magi:Evidence`` matches every record (the
# ontology flattener tags each EvidenceRecord with rdf:type magi:Evidence).
# ``sh:property`` with ``sh:hasValue 0`` on ``magi:field_exitCode`` enforces
# the exitCode==0 invariant.  Conformance ⇔ exitCode==0.
#
# We do NOT add a TestRun-type filter here because every test in this file
# uses a single TestRun record, so the global property check is unambiguous.
_TEST_RUN_EXIT_CODE_ZERO_SHAPE = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

magi:TestRunExitZeroShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path magi:field_exitCode ;
        sh:hasValue 0 ;
        sh:message "TestRun.exitCode must equal 0" ;
    ] .
"""


def _build_customize_json(tmp_path: Path) -> Path:
    """Write a minimal ``customize.json`` carrying our ``shacl_constraint`` rule.

    Shape mirrors the canonical custom-rule envelope used in
    ``tests/test_shacl_custom_rule.py``: a single rule with
    ``what.kind='shacl_constraint'`` and ``what.payload.shapeTtl=<TTL>``.
    The firing test only consumes ``shapeTtl`` from this file, but writing the
    full envelope documents the runtime contract end-to-end.
    """
    rule = {
        "id": "f1-test-run-exit-code-zero",
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "shacl_constraint",
            "payload": {"shapeTtl": _TEST_RUN_EXIT_CODE_ZERO_SHAPE},
        },
        "firesAt": "pre_final",
        "action": "block",
    }
    doc = {"customRules": [rule]}
    path = tmp_path / "customize.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _make_test_run(exit_code: int) -> EvidenceRecord:
    """Synthesise a built-in ``TestRun`` evidence record with given exitCode.

    The ontology flattener emits ``magi:field_exitCode`` for ``fields["exitCode"]``
    (key passes the alphanumeric sanitiser unchanged), and types the integer as
    ``xsd:integer`` -- which matches ``sh:hasValue 0`` in the shape above.
    """
    return EvidenceRecord(
        type="TestRun",
        status="ok",  # SHACL operates on data triples; record.status is unrelated
        observedAt=_OBSERVED_AT,
        source=EvidenceSource(kind="verifier"),
        fields={"exitCode": exit_code},
    )


# ---------------------------------------------------------------------------
# F1 — fires on non-conform input
# ---------------------------------------------------------------------------


def test_shacl_constraint_fires_when_exit_code_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """exitCode=1 must trip the shape -> verifier reports a violation."""
    # Activate the three runtime flags so the test mirrors a real serve env.
    # run_shacl_rule itself does NOT consult these flags (it's a pure function),
    # but flipping them is part of the task contract and exercises the env path
    # that gates the upstream call-site.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")

    # Build the customize.json that an operator would author in a real bot.
    # We only consume shapeTtl from it -- writing the rest documents the
    # storage contract end-to-end.
    customize_path = _build_customize_json(tmp_path)
    raw = json.loads(customize_path.read_text(encoding="utf-8"))
    rule = raw["customRules"][0]
    shape_ttl: str = rule["what"]["payload"]["shapeTtl"]
    rule_id: str = rule["id"]

    nonconforming = [_make_test_run(exit_code=1)]
    result = run_shacl_rule(
        nonconforming,
        shape_ttl,
        rule_id,
        observed_at=_OBSERVED_AT,
    )

    # Verdict: shape violated -> status='failed', conforms=False, >=1 violation.
    assert result.type == "custom:ShaclConstraintCheck"
    assert result.status == "failed", (
        f"Expected status='failed' for non-conforming exitCode=1, got {result.status!r}; "
        f"fields={dict(result.fields)}"
    )
    assert result.fields["ruleId"] == rule_id
    assert result.fields["conforms"] is False
    violations = result.fields["violations"]
    assert isinstance(violations, tuple) and len(violations) >= 1, (
        f"Expected at least one violation tuple entry, got {violations!r}"
    )
    # Spot-check the violation surface: the message we wrote into the shape
    # should appear verbatim on at least one violation entry, proving the
    # shape (not some unrelated SHACL meta-rule) is what fired.
    messages = [v.get("message") for v in violations]
    assert any(
        m == "TestRun.exitCode must equal 0" for m in messages
    ), f"Expected our shape's sh:message in violations, got messages={messages!r}"


# ---------------------------------------------------------------------------
# F1 — passes on conform input (negative control)
# ---------------------------------------------------------------------------


def test_shacl_constraint_passes_when_exit_code_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """exitCode=0 must satisfy the shape -> verifier reports conform/ok.

    This is the negative control for the firing test above: same shape, same
    rule, same env flags -- only the evidence record's exitCode changes.
    Without this assertion a false-positive verifier (one that always fires)
    would still pass the first test.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_SHACL_VERIFIER_ENABLED", "1")

    customize_path = _build_customize_json(tmp_path)
    raw = json.loads(customize_path.read_text(encoding="utf-8"))
    rule = raw["customRules"][0]
    shape_ttl: str = rule["what"]["payload"]["shapeTtl"]
    rule_id: str = rule["id"]

    conforming = [_make_test_run(exit_code=0)]
    result = run_shacl_rule(
        conforming,
        shape_ttl,
        rule_id,
        observed_at=_OBSERVED_AT,
    )

    assert result.type == "custom:ShaclConstraintCheck"
    assert result.status == "ok", (
        f"Expected status='ok' for conforming exitCode=0, got {result.status!r}; "
        f"fields={dict(result.fields)}"
    )
    assert result.fields["ruleId"] == rule_id
    assert result.fields["conforms"] is True
    assert result.fields["violations"] == (), (
        f"Expected no violations for conforming input, got {result.fields['violations']!r}"
    )
