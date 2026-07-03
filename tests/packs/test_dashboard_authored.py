from __future__ import annotations

import pytest

from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DASHBOARD_PACK_ID,
    DashboardCheck,
    DashboardTrigger,
    DashboardTriggerMatch,
)


def test_pack_constants_are_dashboard_namespaced() -> None:
    assert DASHBOARD_PACK_DIR_NAME == "dashboard-authored"
    assert DASHBOARD_PACK_ID == "ext.dashboard.checks"


def test_dashboard_check_minimum_valid_shape() -> None:
    check = DashboardCheck(
        id="ssn-leak",
        label="Block SSN leak from web_fetch",
        scope="always",
        enabled=True,
        trigger=DashboardTrigger(
            tool="web_fetch",
            match=DashboardTriggerMatch(pattern=r"\d{3}-\d{2}-\d{4}", is_regex=True),
        ),
        action="block",
    )
    assert check.id == "ssn-leak"
    assert check.trigger.match.is_regex is True


from magi_agent.packs.dashboard_authored import validate_dashboard_check


def _ok(**over):
    base = {
        "id": "ssn-leak",
        "label": "Block SSN",
        "scope": "always",
        "enabled": True,
        "trigger": {"tool": "web_fetch", "match": {"pattern": "ssn", "isRegex": False}},
        "action": "block",
    }
    base.update(over)
    return base


def test_validate_passes_minimal() -> None:
    assert validate_dashboard_check(_ok()) == []


def test_validate_rejects_uppercase_id() -> None:
    errs = validate_dashboard_check(_ok(id="SSN-leak"))
    assert any("id" in e for e in errs)


def test_validate_rejects_id_too_long() -> None:
    errs = validate_dashboard_check(_ok(id="a" * 64))
    assert any("id" in e for e in errs)


def test_validate_rejects_id_starts_with_hyphen() -> None:
    errs = validate_dashboard_check(_ok(id="-bad"))
    assert any("id" in e for e in errs)


def test_validate_rejects_newline_in_label() -> None:
    errs = validate_dashboard_check(_ok(label="line1\nline2"))
    assert any("label" in e for e in errs)


def test_validate_rejects_oversize_label() -> None:
    errs = validate_dashboard_check(_ok(label="x" * 201))
    assert any("label" in e for e in errs)


def test_validate_rejects_unknown_scope() -> None:
    errs = validate_dashboard_check(_ok(scope="universe"))
    assert any("scope" in e for e in errs)


def test_validate_rejects_unknown_action() -> None:
    errs = validate_dashboard_check(_ok(action="explode"))
    assert any("action" in e for e in errs)


def test_validate_rejects_empty_tool() -> None:
    rule = _ok(); rule["trigger"]["tool"] = ""
    assert any("tool" in e for e in validate_dashboard_check(rule))


def test_validate_rejects_empty_pattern() -> None:
    rule = _ok(); rule["trigger"]["match"]["pattern"] = ""
    assert any("pattern" in e for e in validate_dashboard_check(rule))


def test_validate_rejects_oversize_pattern() -> None:
    rule = _ok(); rule["trigger"]["match"]["pattern"] = "x" * 501
    assert any("pattern" in e for e in validate_dashboard_check(rule))


def test_validate_rejects_invalid_regex() -> None:
    rule = _ok(); rule["trigger"]["match"] = {"pattern": "([unclosed", "isRegex": True}
    assert any("regex" in e.lower() for e in validate_dashboard_check(rule))


def test_validate_rejects_catastrophic_regex() -> None:
    # Heuristic: nested quantifiers like (.+)+ are commonly catastrophic.
    rule = _ok(); rule["trigger"]["match"] = {"pattern": "(.+)+x", "isRegex": True}
    assert any("regex" in e.lower() for e in validate_dashboard_check(rule))


from magi_agent.packs.dashboard_authored import slug_of


def test_slug_of_simple_lowercased() -> None:
    assert slug_of("Block SSN leak") == "block-ssn-leak"


def test_slug_of_strips_non_alphanumeric() -> None:
    assert slug_of("API key!! (sensitive)") == "api-key-sensitive"


def test_slug_of_collision_suffix() -> None:
    existing = {"my-check"}
    assert slug_of("My check", taken=existing) == "my-check-2"


def test_slug_of_multiple_collisions() -> None:
    existing = {"x", "x-2", "x-3"}
    assert slug_of("X", taken=existing) == "x-4"


def test_slug_of_empty_label_falls_back() -> None:
    assert slug_of("!!!") == "check"


from magi_agent.packs.dashboard_authored import (
    compile_recipe,
)


def _check(id_: str = "ssn-leak", *, action="block", enabled=True):
    return DashboardCheck.model_validate(_ok(id=id_, action=action, enabled=enabled))


def test_compile_recipe_is_declarative_only_empty_evidence_refs() -> None:
    # Enforcement moved to producer+gate (deny-on-present). The recipe pack is a
    # declarative namespace artifact and carries NO required evidence refs — even
    # for enabled block checks (a required ref would be inert AND invert polarity).
    checks = [_check("ssn-leak"), _check("api-key-leak"), _check("disabled-one", enabled=False)]
    manifest = compile_recipe(checks)
    assert tuple(manifest.evidence_refs) == ()


def test_compile_recipe_uses_dashboard_pack_id_and_not_default_enabled() -> None:
    manifest = compile_recipe([_check("x")])
    assert manifest.pack_id == DASHBOARD_PACK_ID
    assert manifest.default_enabled is False
    assert manifest.hard_safety is False


def test_compile_recipe_block_and_audit_both_yield_no_required_refs() -> None:
    # Neither block nor audit checks add required refs — enforcement is via the
    # producer + verifier-bus deny-on-present gate, not required-evidence.
    checks = [_check("blocker", action="block"), _check("auditor", action="audit")]
    manifest = compile_recipe(checks)
    assert tuple(manifest.evidence_refs) == ()


def test_compile_recipe_empty_checks_returns_empty_evidence_refs() -> None:
    manifest = compile_recipe([])
    assert tuple(manifest.evidence_refs) == ()


from magi_agent.recipes.kernel_recipe_packs import validate_external_recipe_pack


def test_compiled_recipe_passes_external_pack_validation() -> None:
    manifest = compile_recipe([_check("x"), _check("y")])
    assert validate_external_recipe_pack(manifest) == ""


import json
from pathlib import Path

from magi_agent.packs.dashboard_authored import (
    read_sidecar,
    write_pack,
)


def test_write_pack_creates_directory_and_files(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("x")])
    assert (pack_root / "pack.toml").exists()
    assert (pack_root / "checks.recipe.toml").exists()
    assert (pack_root / "dashboard-checks.json").exists()


def test_write_pack_empty_removes_directory(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("x")])
    write_pack(pack_root, [])
    assert not pack_root.exists()


def test_write_pack_pack_toml_references_recipe_spec(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("x")])
    body = (pack_root / "pack.toml").read_text()
    assert 'packId = "ext.dashboard.checks"' in body
    assert 'type = "recipe"' in body
    assert 'spec = "checks.recipe.toml"' in body


def test_sidecar_contains_all_checks(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("a"), _check("b")])
    sidecar = json.loads((pack_root / "dashboard-checks.json").read_text())
    ids = {c["id"] for c in sidecar}
    assert ids == {"a", "b"}


def test_read_sidecar_round_trip(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    checks_in = [_check("a"), _check("b", action="audit")]
    write_pack(pack_root, checks_in)
    checks_out = read_sidecar(pack_root)
    assert {c.id for c in checks_out} == {"a", "b"}
    by_id = {c.id: c for c in checks_out}
    assert by_id["b"].action == "audit"


def test_read_sidecar_missing_returns_empty(tmp_path: Path) -> None:
    assert read_sidecar(tmp_path / "absent") == []


def test_write_pack_atomic_recipe_spec_never_partial(tmp_path: Path) -> None:
    # After a successful write, no mkstemp .tmp leftover should remain, and the
    # directory holds exactly the three expected files.
    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("x")])
    names = {p.name for p in pack_root.iterdir()}
    assert not any(n.endswith(".tmp") for n in names)
    assert names == {"pack.toml", "checks.recipe.toml", "dashboard-checks.json"}


# --- schema hardening (defense-in-depth) ---


def test_dashboard_check_id_field_validator_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        DashboardCheck.model_validate(_ok(id="../../etc/passwd"))


def test_validate_rejects_unknown_top_level_key() -> None:
    rule = _ok()
    rule["surprise"] = 1
    errs = validate_dashboard_check(rule)
    assert any("unknown key" in e and "surprise" in e for e in errs)


def test_validate_rejects_unknown_key_under_trigger_match() -> None:
    rule = _ok()
    rule["trigger"]["match"]["bonus"] = True
    errs = validate_dashboard_check(rule)
    assert any("unknown key" in e and "bonus" in e for e in errs)


# --- accept-boundary tests (exact-cap values pass) ---


def test_validate_accepts_id_exactly_63_chars() -> None:
    assert validate_dashboard_check(_ok(id="a" * 63)) == []


def test_validate_accepts_label_exactly_200_chars() -> None:
    assert validate_dashboard_check(_ok(label="x" * 200)) == []


def test_validate_accepts_pattern_exactly_500_chars() -> None:
    rule = _ok()
    rule["trigger"]["match"]["pattern"] = "x" * 500
    assert validate_dashboard_check(rule) == []


def test_round_trip_carries_disabled_and_regex_flags(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    check = DashboardCheck.model_validate(
        _ok(
            id="rx",
            enabled=False,
            trigger={"tool": "web_fetch", "match": {"pattern": "ssn", "isRegex": True}},
        )
    )
    write_pack(pack_root, [check])
    out = read_sidecar(pack_root)
    assert len(out) == 1
    assert out[0].enabled is False
    assert out[0].trigger.match.is_regex is True


def test_read_sidecar_skips_entry_with_unknown_key(tmp_path: Path) -> None:
    pack_root = tmp_path / "dashboard-authored"
    pack_root.mkdir(parents=True)
    valid = _ok(id="good")
    malformed = _ok(id="bad")
    malformed["surprise"] = True  # extra="forbid" → model_validate raises → skipped
    (pack_root / "dashboard-checks.json").write_text(json.dumps([valid, malformed]))
    out = read_sidecar(pack_root)
    assert {c.id for c in out} == {"good"}


# --- discovery-format round trip (guards the TOML blocker) ---


def test_recipe_spec_round_trips_through_parse_recipe_manifest(tmp_path: Path) -> None:
    from magi_agent.recipes.kernel_recipe_packs import parse_recipe_manifest

    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("ssn-leak")])
    parsed = parse_recipe_manifest(pack_root / "checks.recipe.toml")
    assert parsed is not None
    assert parsed.pack_id == "ext.dashboard.checks"
    assert parsed.default_enabled is False
    # Declarative-only: enforcement is deny-on-present (producer+gate), so the
    # recipe spec round-trips with empty evidenceRefs.
    assert tuple(parsed.evidence_refs) == ()
    assert validate_external_recipe_pack(parsed) == ""


# --- emitsEvidenceType + arguments-based domainAllowlist trigger (2b) ---


def test_validate_passes_domain_allowlist_trigger() -> None:
    check = _ok(
        trigger={"tool": "web_fetch", "domainAllowlist": ["sec.gov"]},
        action="audit",
        emitsEvidenceType="custom:SourceCredibility",
    )
    assert validate_dashboard_check(check) == []


def test_validate_passes_emits_type_on_result_text() -> None:
    assert validate_dashboard_check(_ok(emitsEvidenceType="custom:PiiSeen")) == []


def test_validate_rejects_bad_emits_type() -> None:
    errs = validate_dashboard_check(_ok(emitsEvidenceType="source_credibility"))
    assert any("emitsEvidenceType" in e for e in errs)


def test_validate_rejects_builtin_emits_type() -> None:
    # A dashboard producer may only emit a custom: type. A trusted builtin name
    # (e.g. TestRun) must be rejected so a domain-allowlist producer cannot mint
    # a record typed as a runtime-reserved evidence family (N1, PR-2b review).
    for builtin in ("TestRun", "WebSearch", "KnowledgeSearch"):
        errs = validate_dashboard_check(_ok(emitsEvidenceType=builtin))
        assert any("emitsEvidenceType" in e for e in errs), builtin


def test_dashboard_check_model_rejects_builtin_emits_type() -> None:
    with pytest.raises(ValueError, match="custom:"):
        DashboardCheck.model_validate(_ok(emitsEvidenceType="TestRun"))


def test_validate_rejects_trigger_without_match_or_domain() -> None:
    errs = validate_dashboard_check(_ok(trigger={"tool": "web_fetch"}))
    assert any("match or a domainAllowlist" in e for e in errs)


def test_validate_rejects_empty_domain_allowlist() -> None:
    errs = validate_dashboard_check(
        _ok(trigger={"tool": "web_fetch", "domainAllowlist": []})
    )
    assert any("domainAllowlist" in e for e in errs)


def test_validate_rejects_non_string_domain_allowlist_entry() -> None:
    errs = validate_dashboard_check(
        _ok(trigger={"tool": "web_fetch", "domainAllowlist": ["sec.gov", 42]})
    )
    assert any("domainAllowlist" in e for e in errs)
