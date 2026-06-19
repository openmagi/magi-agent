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
    DASHBOARD_EVIDENCE_REF_PREFIX,
    compile_recipe,
)


def _check(id_: str = "ssn-leak", *, action="block", enabled=True):
    return DashboardCheck.model_validate(_ok(id=id_, action=action, enabled=enabled))


def test_compile_recipe_evidence_refs_namespaced_per_enabled_check() -> None:
    checks = [_check("ssn-leak"), _check("api-key-leak"), _check("disabled-one", enabled=False)]
    manifest = compile_recipe(checks)
    refs = set(manifest.evidence_refs)
    assert f"{DASHBOARD_EVIDENCE_REF_PREFIX}ssn-leak" in refs
    assert f"{DASHBOARD_EVIDENCE_REF_PREFIX}api-key-leak" in refs
    # disabled checks do not require evidence (so they never block the gate)
    assert f"{DASHBOARD_EVIDENCE_REF_PREFIX}disabled-one" not in refs


def test_compile_recipe_uses_dashboard_pack_id_and_not_default_enabled() -> None:
    manifest = compile_recipe([_check("x")])
    assert manifest.pack_id == DASHBOARD_PACK_ID
    assert manifest.default_enabled is False
    assert manifest.hard_safety is False


def test_compile_recipe_only_block_action_adds_required_ref() -> None:
    # 'audit' action SHOULD still emit the ref via producer, but MUST NOT block;
    # validator side does not include audit-only refs in evidence_refs.
    checks = [_check("blocker", action="block"), _check("auditor", action="audit")]
    manifest = compile_recipe(checks)
    refs = set(manifest.evidence_refs)
    assert f"{DASHBOARD_EVIDENCE_REF_PREFIX}blocker" in refs
    assert f"{DASHBOARD_EVIDENCE_REF_PREFIX}auditor" not in refs


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
    assert (pack_root / "checks.recipe.json").exists()
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
    assert 'spec = "checks.recipe.json"' in body


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
    # After a successful write, no .tmp file should remain.
    pack_root = tmp_path / "dashboard-authored"
    write_pack(pack_root, [_check("x")])
    leftovers = [p.name for p in pack_root.iterdir() if p.name.startswith(".")]
    assert leftovers == []
