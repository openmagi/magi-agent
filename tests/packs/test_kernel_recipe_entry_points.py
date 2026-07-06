"""Tests for the ``magi.recipes`` Python ``entry_points`` source on top of the
kernel recipe-pack registry.

Default-OFF (``MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED``) AND'd with the existing
kernel-packs flag. Self-host trust model: ``EntryPoint.load()`` imports the
publisher's module, so this is the standard distribution-tool trust boundary —
inert DATA payloads only (callable / code-carrying payloads are dropped).
"""
from __future__ import annotations

import pytest

from magi_agent.recipes.compiler import PackRegistry
from magi_agent.recipes.kernel_recipe_packs import (
    MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED_ENV as EP_FLAG,
    MAGI_KERNEL_RECIPE_PACKS_ENABLED_ENV as KERNEL_FLAG,
    RECIPE_ENTRY_POINT_GROUP,
    _coerce_entry_point_payload,
    build_runtime_pack_registry,
)


def _valid_manifest_dict(pack_id: str = "ext.acme.recipe") -> dict:
    return {
        "packId": pack_id,
        "version": "1",
        "displayName": "ext recipe via entry_points",
        "description": "A distributable declarative recipe pack.",
        "defaultEnabled": False,
    }


class _FakeEntryPoint:
    def __init__(self, name: str, value):
        self.name = name
        self._value = value

    def load(self):
        if isinstance(self._value, BaseException):
            raise self._value
        return self._value


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, eps: list[_FakeEntryPoint]) -> None:
    # importlib.metadata.entry_points(group=...) — patch the call inside the module
    # under test so we don't depend on installed distributions.
    def _fake_entry_points(*, group: str):
        return tuple(eps) if group == RECIPE_ENTRY_POINT_GROUP else ()

    monkeypatch.setattr(
        "magi_agent.recipes.kernel_recipe_packs.importlib_metadata.entry_points",
        _fake_entry_points,
    )


# --------------------------------------------------------------------------- #
# _coerce_entry_point_payload — accepts only inert DATA
# --------------------------------------------------------------------------- #
def test_coerce_accepts_dict() -> None:
    assert _coerce_entry_point_payload({"packId": "ext.x"}) == {"packId": "ext.x"}


def test_coerce_rejects_callable() -> None:
    assert _coerce_entry_point_payload(lambda: {"packId": "ext.x"}) is None


def test_coerce_rejects_class() -> None:
    class Recipe:
        pass

    # classes are callable → rejected (no smuggled instantiation)
    assert _coerce_entry_point_payload(Recipe) is None


def test_coerce_accepts_pydantic_model_dump() -> None:
    class Stub:
        def model_dump(self, **_kw):
            return {"packId": "ext.x"}

    assert _coerce_entry_point_payload(Stub()) == {"packId": "ext.x"}


def test_coerce_rejects_none() -> None:
    assert _coerce_entry_point_payload(None) is None


# --------------------------------------------------------------------------- #
# Flag matrix — both flags must be ON
# --------------------------------------------------------------------------- #
def test_entry_points_inert_when_ep_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.delenv(EP_FLAG, raising=False)
    # Avoid picking up real user packs on the developer's machine.
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", _valid_manifest_dict())])
    assert (
        build_runtime_pack_registry().pack_ids
        == PackRegistry.with_first_party_packs().pack_ids
    )


def test_entry_points_inert_when_kernel_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "0")
    monkeypatch.setenv(EP_FLAG, "1")
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", _valid_manifest_dict())])
    # Kernel flag off → outer guard exits before the entry_points loop.
    assert (
        build_runtime_pack_registry().pack_ids
        == PackRegistry.with_first_party_packs().pack_ids
    )


def test_entry_points_register_when_both_flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.setenv(EP_FLAG, "1")
    # Avoid picking up unrelated user packs on the developer's machine.
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint("acme", _valid_manifest_dict("ext.acme.recipe"))]
    )
    ids = build_runtime_pack_registry().pack_ids
    assert "ext.acme.recipe" in ids
    # First-party packs preserved.
    for fp in PackRegistry.with_first_party_packs().pack_ids:
        assert fp in ids


# --------------------------------------------------------------------------- #
# Safety: callable / failing / invalid entries are dropped, never raise
# --------------------------------------------------------------------------- #
def test_callable_entry_point_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.setenv(EP_FLAG, "1")
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint("evil", lambda: {"packId": "ext.evil"})]
    )
    ids = build_runtime_pack_registry().pack_ids
    assert "ext.evil" not in ids
    assert ids == PackRegistry.with_first_party_packs().pack_ids


def test_failing_load_does_not_halt_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.setenv(EP_FLAG, "1")
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint("broken", RuntimeError("import explode")),
            _FakeEntryPoint("good", _valid_manifest_dict("ext.acme.good")),
        ],
    )
    ids = build_runtime_pack_registry().pack_ids
    assert "ext.acme.good" in ids


def test_invalid_manifest_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.setenv(EP_FLAG, "1")
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    _patch_entry_points(
        monkeypatch,
        [_FakeEntryPoint("malformed", {"not": "a pack manifest"})],
    )
    assert (
        build_runtime_pack_registry().pack_ids
        == PackRegistry.with_first_party_packs().pack_ids
    )


# --------------------------------------------------------------------------- #
# Trust boundary — entry_points publishers are UNTRUSTED (compose-only checks)
# --------------------------------------------------------------------------- #
def test_non_ext_namespace_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.setenv(EP_FLAG, "1")
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    # Missing ``ext.`` prefix → R1 namespace violation → dropped.
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint("naked", _valid_manifest_dict("acme.recipe"))]
    )
    ids = build_runtime_pack_registry().pack_ids
    assert "acme.recipe" not in ids
    assert ids == PackRegistry.with_first_party_packs().pack_ids


def test_default_enabled_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KERNEL_FLAG, "1")
    monkeypatch.setenv(EP_FLAG, "1")
    monkeypatch.setattr("magi_agent.packs.discovery.default_search_bases", list)
    manifest = _valid_manifest_dict("ext.acme.eager")
    manifest["defaultEnabled"] = True  # R7 violation
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("eager", manifest)])
    ids = build_runtime_pack_registry().pack_ids
    assert "ext.acme.eager" not in ids
