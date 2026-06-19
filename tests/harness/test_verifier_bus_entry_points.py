"""Tests for the ``magi.verifiers`` Python ``entry_points`` source on top of the
default verifier bus.

Default-OFF (``MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED``). Self-host trust
model: ``EntryPoint.load()`` imports the publisher's module, so inert DATA
payloads only (callable / code-carrying payloads are dropped).
"""
from __future__ import annotations

import pytest

from magi_agent.harness.verifier_bus import (
    MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED_ENV as FLAG,
    VERIFIER_ENTRY_POINT_GROUP,
    VerifierMetadata,
    _coerce_verifier_payload,
    build_default_verifier_bus_metadata,
    build_runtime_verifier_bus_metadata,
    with_additional_verifiers,
)


def _valid_verifier_manifest(
    verifier_id: str = "ext-acme-citation-extra", *, priority: int = 500
) -> dict:
    return {
        "verifierId": verifier_id,
        "stage": "source_claim_link",
        "phase": "deterministic",
        "priority": priority,
        "description": "Extra citation completeness check.",
        "defaultEnabled": False,
        "disabled": True,
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
    def _fake_entry_points(*, group: str):
        return tuple(eps) if group == VERIFIER_ENTRY_POINT_GROUP else ()

    # The loader imports importlib_metadata locally; patch the public module.
    monkeypatch.setattr(
        "importlib.metadata.entry_points", _fake_entry_points, raising=True
    )


# --------------------------------------------------------------------------- #
# Payload coercion — accepts inert DATA only
# --------------------------------------------------------------------------- #
def test_coerce_accepts_dict() -> None:
    assert _coerce_verifier_payload({"verifierId": "x"}) == {"verifierId": "x"}


def test_coerce_rejects_callable() -> None:
    assert _coerce_verifier_payload(lambda: {"verifierId": "x"}) is None


def test_coerce_rejects_class() -> None:
    class Stub:
        pass

    assert _coerce_verifier_payload(Stub) is None


def test_coerce_accepts_pydantic_model_dump() -> None:
    class Stub:
        def model_dump(self, **_kw):
            return {"verifierId": "x"}

    assert _coerce_verifier_payload(Stub()) == {"verifierId": "x"}


# --------------------------------------------------------------------------- #
# Flag gating
# --------------------------------------------------------------------------- #
def test_flag_off_is_byte_identical_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint("acme", _valid_verifier_manifest())]
    )
    base = build_default_verifier_bus_metadata()
    runtime = build_runtime_verifier_bus_metadata()
    # Same verifier set — discovery never ran.
    assert {v.verifier_id for v in runtime.verifiers} == {
        v.verifier_id for v in base.verifiers
    }


def test_flag_on_external_verifier_joins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    _patch_entry_points(
        monkeypatch,
        [_FakeEntryPoint("acme", _valid_verifier_manifest("ext-acme-extra-check"))],
    )
    bus = build_runtime_verifier_bus_metadata()
    ids = {v.verifier_id for v in bus.verifiers}
    assert "ext-acme-extra-check" in ids
    # First-party verifiers all preserved (tighten-only is additive).
    base_ids = {v.verifier_id for v in build_default_verifier_bus_metadata().verifiers}
    assert base_ids.issubset(ids)


# --------------------------------------------------------------------------- #
# Tighten-only: V1 / V4 / V5 + hard-safety claim rejection
# --------------------------------------------------------------------------- #
def test_v1_protected_hard_safety_cannot_be_overwritten() -> None:
    base = build_default_verifier_bus_metadata()
    impostor = VerifierMetadata.model_validate(
        {
            "verifierId": "security-policy-hard-safety",  # the protected id
            "stage": "source_claim_link",
            "phase": "deterministic",
            "priority": 5000,
            "description": "shadow attempt",
            "defaultEnabled": False,
            "disabled": True,
        }
    )
    merged = with_additional_verifiers(base, [impostor])
    # The protected id keeps its original first-party entry.
    by_id = {v.verifier_id: v for v in merged.verifiers}
    assert by_id["security-policy-hard-safety"].hard_safety is True
    # The impostor's bogus priority is not present at the protected id.
    assert by_id["security-policy-hard-safety"].priority <= 60


def test_v4_external_cannot_replace_existing_id() -> None:
    base = build_default_verifier_bus_metadata()
    any_existing_id = next(iter(v.verifier_id for v in base.verifiers))
    duplicate = VerifierMetadata.model_validate(
        _valid_verifier_manifest(verifier_id=any_existing_id, priority=999)
    )
    merged = with_additional_verifiers(base, [duplicate])
    # No replacement: same verifier count and the original entry is unchanged.
    assert len(merged.verifiers) == len(base.verifiers)


def test_v5_priority_in_hard_safety_band_dropped() -> None:
    base = build_default_verifier_bus_metadata()
    intruder = VerifierMetadata.model_validate(
        _valid_verifier_manifest("ext-band-invader", priority=60)  # == protected band
    )
    merged = with_additional_verifiers(base, [intruder])
    assert "ext-band-invader" not in {v.verifier_id for v in merged.verifiers}


def test_external_claiming_hard_safety_dropped() -> None:
    base = build_default_verifier_bus_metadata()
    claim = VerifierMetadata.model_validate(
        {
            "verifierId": "ext-acme-allegedly-safety",
            "stage": "security_policy",
            "phase": "deterministic",
            "priority": 500,
            "description": "claims hard safety",
            "hardSafety": True,  # forbidden for externals
            "securityCritical": True,
        }
    )
    merged = with_additional_verifiers(base, [claim])
    assert "ext-acme-allegedly-safety" not in {v.verifier_id for v in merged.verifiers}


def test_external_duplicate_ids_first_wins() -> None:
    base = build_default_verifier_bus_metadata()
    a = VerifierMetadata.model_validate(
        _valid_verifier_manifest("ext-dup", priority=500)
    )
    b = VerifierMetadata.model_validate(
        _valid_verifier_manifest("ext-dup", priority=900)
    )
    merged = with_additional_verifiers(base, [a, b])
    matches = [v for v in merged.verifiers if v.verifier_id == "ext-dup"]
    assert len(matches) == 1
    assert matches[0].priority == 500


# --------------------------------------------------------------------------- #
# Discovery safety: callable / failing / malformed entries never raise
# --------------------------------------------------------------------------- #
def test_callable_entry_point_payload_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    _patch_entry_points(
        monkeypatch,
        [_FakeEntryPoint("evil", lambda: _valid_verifier_manifest("ext-evil"))],
    )
    bus = build_runtime_verifier_bus_metadata()
    assert "ext-evil" not in {v.verifier_id for v in bus.verifiers}


def test_failing_load_does_not_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint("broken", RuntimeError("import explode")),
            _FakeEntryPoint("good", _valid_verifier_manifest("ext-good")),
        ],
    )
    bus = build_runtime_verifier_bus_metadata()
    assert "ext-good" in {v.verifier_id for v in bus.verifiers}


def test_malformed_manifest_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    _patch_entry_points(
        monkeypatch,
        [_FakeEntryPoint("malformed", {"not": "a verifier"})],
    )
    base_ids = {v.verifier_id for v in build_default_verifier_bus_metadata().verifiers}
    bus_ids = {v.verifier_id for v in build_runtime_verifier_bus_metadata().verifiers}
    assert bus_ids == base_ids


def test_sequence_payload_admits_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    payload = [
        _valid_verifier_manifest("ext-a"),
        _valid_verifier_manifest("ext-b"),
    ]
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("multi", payload)])
    bus = build_runtime_verifier_bus_metadata()
    ids = {v.verifier_id for v in bus.verifiers}
    assert "ext-a" in ids and "ext-b" in ids
