"""Integration: dashboard-authored pack on disk → kernel pack registry → gate refs."""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DASHBOARD_PACK_ID,
    DashboardCheck,
    write_pack,
)
from magi_agent.recipes.compiler import PackRegistry
from magi_agent.recipes.kernel_recipe_packs import (
    MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV as KERNEL_FLAG,
    build_runtime_pack_registry,
)


def _check(id_: str = "ssn-leak", action="block"):
    return DashboardCheck.model_validate({
        "id": id_, "label": "x", "scope": "always", "enabled": True,
        "trigger": {"tool": "web_fetch", "match": {"pattern": "ssn", "isRegex": False}},
        "action": action,
    })


def _patch_bases(monkeypatch: pytest.MonkeyPatch, bases: list[Path]) -> None:
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases", lambda: list(bases)
    )


def test_dashboard_pack_appears_in_registry_when_kernel_flag_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    write_pack(pack_root, [_check("ssn-leak"), _check("api-key", action="audit")])
    _patch_bases(monkeypatch, [tmp_path])
    registry = build_runtime_pack_registry()
    assert DASHBOARD_PACK_ID in registry.pack_ids


def test_dashboard_pack_in_registry_with_empty_evidence_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Enforcement is deny-on-present (producer+gate), so the discovered recipe
    # pack appears in the registry but carries NO required evidence refs — for
    # block or audit checks alike.
    monkeypatch.setenv(KERNEL_FLAG, "1")
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    write_pack(pack_root, [_check("blocker"), _check("auditor", action="audit")])
    _patch_bases(monkeypatch, [tmp_path])
    registry = build_runtime_pack_registry()
    assert DASHBOARD_PACK_ID in registry.pack_ids
    manifest = registry.get(DASHBOARD_PACK_ID)
    assert tuple(manifest.evidence_refs) == ()
    # first-party packs are not shadowed by the discovered dashboard pack
    for fp in PackRegistry.with_first_party_packs().pack_ids:
        assert fp in registry.pack_ids


def test_dashboard_pack_absent_when_kernel_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(KERNEL_FLAG, raising=False)
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    write_pack(pack_root, [_check("x")])
    _patch_bases(monkeypatch, [tmp_path])
    registry = build_runtime_pack_registry()
    assert registry.pack_ids == PackRegistry.with_first_party_packs().pack_ids


def test_empty_pack_directory_invisible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    write_pack(pack_root, [_check("x")])
    write_pack(pack_root, [])  # remove
    _patch_bases(monkeypatch, [tmp_path])
    registry = build_runtime_pack_registry()
    assert DASHBOARD_PACK_ID not in registry.pack_ids


def test_first_party_pack_collision_does_not_shadow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dashboard pack cannot shadow a first-party pack — ext.* prefix prevents
    collision at namespace level (R1)."""
    monkeypatch.setenv(KERNEL_FLAG, "1")
    pack_root = tmp_path / DASHBOARD_PACK_DIR_NAME
    write_pack(pack_root, [_check("x")])
    _patch_bases(monkeypatch, [tmp_path])
    registry = build_runtime_pack_registry()
    for fp in PackRegistry.with_first_party_packs().pack_ids:
        assert fp in registry.pack_ids  # all first-party preserved
