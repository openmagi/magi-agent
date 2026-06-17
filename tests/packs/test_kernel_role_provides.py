"""PR2 — ``role`` as the kernel's 9th provides type (contained scope label).

A user adds an ``ext.<name>`` agent role via ``pack.toml [[provides]] type="role"``
+ a declarative ``RoleManifest`` spec. Covers: the namespace trust boundary,
flag-OFF byte-identical baseline, flag-ON harness recognition, first-party-wins /
impersonation rejection, the hard-safety invariant, evidence-scope containment,
and the kernel projection into ``registries.roles``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.harness.kernel_roles import (
    FIRST_PARTY_AGENT_ROLE_IDS,
    MAGI_KERNEL_ROLE_PROVIDES_ENABLED_ENV as FLAG,
)
from magi_agent.harness.kernel_roles import (
    known_agent_role_ids,
    validate_external_role_id,
)
from magi_agent.harness.resolved import (
    _build_evidence_scope_context,
    _default_effective_harness_packs,
    build_default_resolved_harness_state,
)


def _write_role_pack(base: Path, *, role_id: str, ref: str = "role:ext-test@1") -> None:
    pack_dir = base / "user_role_pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.toml").write_text(
        'packId = "pack.user-role"\n'
        'displayName = "User role pack"\n'
        'version = "1.0.0"\n'
        'description = "A user-authored role pack for testing."\n\n'
        "[[provides]]\n"
        'type = "role"\n'
        f'ref = "{ref}"\n'
        'spec = "user.role.toml"\n',
        encoding="utf-8",
    )
    (pack_dir / "user.role.toml").write_text(
        f'roleId = "{role_id}"\n'
        'displayName = "User role"\n'
        'description = "A user-authored scope label."\n',
        encoding="utf-8",
    )


def _patch_bases(monkeypatch: pytest.MonkeyPatch, bases: list[Path]) -> None:
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: list(bases)
    )


# --------------------------------------------------------------------------- #
# validate_external_role_id — the trust boundary
# --------------------------------------------------------------------------- #
def test_validate_accepts_ext_namespaced_role() -> None:
    assert validate_external_role_id("ext.finance") == ""
    assert validate_external_role_id("ext.acme.finance-grounding") == ""


def test_validate_rejects_first_party_impersonation() -> None:
    for role in FIRST_PARTY_AGENT_ROLE_IDS:
        assert validate_external_role_id(role) == "first_party_collision"


def test_validate_rejects_non_ext_and_malformed() -> None:
    assert validate_external_role_id("finance") == "namespace_required"
    assert validate_external_role_id("ext.") == "malformed_role_id"
    assert validate_external_role_id(123) == "role_id_not_a_string"


# --------------------------------------------------------------------------- #
# Flag OFF — byte-identical baseline
# --------------------------------------------------------------------------- #
def test_known_roles_flag_off_is_exactly_first_party(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    assert known_agent_role_ids() == frozenset(FIRST_PARTY_AGENT_ROLE_IDS)


def test_flag_off_ignores_a_present_role_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    _write_role_pack(tmp_path, role_id="ext.finance")
    _patch_bases(monkeypatch, [tmp_path])
    assert known_agent_role_ids() == frozenset(FIRST_PARTY_AGENT_ROLE_IDS)


def test_resolution_flag_off_rejects_external_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    with pytest.raises(Exception):
        build_default_resolved_harness_state(agent_role="ext.finance")


def test_resolution_flag_off_first_party_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    for role in FIRST_PARTY_AGENT_ROLE_IDS:
        state = build_default_resolved_harness_state(agent_role=role, spawn_depth=1)
        assert state.agent_role == role
        assert state.effective_harness_packs == (role, "hard-safety")


# --------------------------------------------------------------------------- #
# Flag ON — external role via pack.toml joins
# --------------------------------------------------------------------------- #
def test_flag_on_role_pack_joins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_role_pack(tmp_path, role_id="ext.finance")
    _patch_bases(monkeypatch, [tmp_path])
    assert known_agent_role_ids() == frozenset(FIRST_PARTY_AGENT_ROLE_IDS) | {"ext.finance"}


def test_flag_on_external_role_resolves_with_hard_safety(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_role_pack(tmp_path, role_id="ext.finance")
    _patch_bases(monkeypatch, [tmp_path])
    state = build_default_resolved_harness_state(agent_role="ext.finance", spawn_depth=1)
    assert state.agent_role == "ext.finance"
    assert state.effective_harness_packs == ("ext.finance", "hard-safety")


# --------------------------------------------------------------------------- #
# Flag ON — adversarial: impersonation / malformed dropped
# --------------------------------------------------------------------------- #
def test_flag_on_first_party_impersonation_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_role_pack(tmp_path, role_id="coding")  # impersonation attempt
    _patch_bases(monkeypatch, [tmp_path])
    assert known_agent_role_ids() == frozenset(FIRST_PARTY_AGENT_ROLE_IDS)


def test_flag_on_non_ext_and_malformed_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(FLAG, "1")
    _write_role_pack(tmp_path, role_id="finance")  # missing ext. namespace
    (tmp_path / "user_role_pack" / "user.role.toml").write_text(
        'roleId = "finance"\n', encoding="utf-8"
    )
    _patch_bases(monkeypatch, [tmp_path])
    assert known_agent_role_ids() == frozenset(FIRST_PARTY_AGENT_ROLE_IDS)


# --------------------------------------------------------------------------- #
# Hard-safety invariant + evidence-scope containment
# --------------------------------------------------------------------------- #
def test_unknown_role_child_pack_is_only_hard_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    assert _default_effective_harness_packs(run_on="child", agent_role="ext.unknown") == (
        "hard-safety",
    )


def test_external_role_gets_no_first_party_evidence_scope_context() -> None:
    assert (
        _build_evidence_scope_context(agent_role="ext.finance", run_on="child", spawn_depth=1)
        is None
    )
    assert (
        _build_evidence_scope_context(agent_role="coding", run_on="child", spawn_depth=1)
        is not None
    )


# --------------------------------------------------------------------------- #
# Kernel projection — role provides reaches registries.roles
# --------------------------------------------------------------------------- #
def test_role_provides_projects_into_registries(tmp_path: Path) -> None:
    from magi_agent.harness.kernel_roles import RoleManifest
    from magi_agent.packs.registries import load_into_registries

    _write_role_pack(tmp_path, role_id="ext.finance", ref="role:ext-finance@1")
    registries, _ = load_into_registries([tmp_path])
    manifest = registries.roles.resolve("role:ext-finance@1")
    assert isinstance(manifest, RoleManifest)
    assert manifest.role_id == "ext.finance"
