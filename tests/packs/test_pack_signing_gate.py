"""Pack signing / digest verification gate (curated trust model "A").

The gate is the SAFE OSS foundation of a hosted third-party pack ecosystem:
only packs whose content digest is in an operator allowlist load, and only when
the operator opts in via ``MAGI_PACK_SIGNING_REQUIRED``. Default-OFF is
byte-identical: no digest is computed and every discovered pack flows through.

Bundled first-party packs (``magi_agent/firstparty/packs``, pack_id prefix
``openmagi.``) are trusted by being bundled and must NEVER be dropped by the gate.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.packs.discovery import (
    DiscoveredPack,
    discover_pack_files,
)
from magi_agent.packs.signing import (
    compute_pack_digest,
    filter_trusted_packs,
    pack_digest_trusted,
)

_USER_PACK_TOML = """\
packId = "user.signing-validator-pack"
displayName = "User Signing Validator Pack"
version = "0.1.0"
description = "User validator pack for the signing-gate test."

[[provides]]
type = "validator"
ref = "validator:userSigned@1"
impl = "user_signing_validator_pack.impl:validate"
"""

_USER_IMPL_PY = '''\
"""User validator impl for the signing-gate test."""
from __future__ import annotations


def validate(ctx):  # pragma: no cover - never executed in this test
    return None
'''

_BUNDLED_PACK_TOML = """\
packId = "openmagi.signing-bundled-pack"
displayName = "Bundled Signing Pack"
version = "0.1.0"
description = "First-party-style bundled pack for the signing-gate test."

[[provides]]
type = "validator"
ref = "validator:bundledSigned@1"
impl = "openmagi_signing_bundled_pack.impl:validate"
"""

_BUNDLED_IMPL_PY = '''\
"""Bundled validator impl for the signing-gate test."""
from __future__ import annotations


def validate(ctx):  # pragma: no cover - never executed in this test
    return None
'''


def _write_pack(base: Path, dir_name: str, toml: str, impl: str) -> Path:
    pack_dir = base / dir_name
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(toml)
    (pack_dir / "impl.py").write_text(impl)
    return pack_dir


def _discover_one(base: Path) -> DiscoveredPack:
    discovered = discover_pack_files([base])
    assert len(discovered) == 1, discovered
    return discovered[0]


def test_digest_is_stable(tmp_path: Path) -> None:
    base = tmp_path / "packs"
    _write_pack(base, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    pack = _discover_one(base)
    assert compute_pack_digest(pack) == compute_pack_digest(pack)


def test_digest_is_content_sensitive(tmp_path: Path) -> None:
    base_a = tmp_path / "a"
    base_b = tmp_path / "b"
    _write_pack(base_a, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    _write_pack(
        base_b,
        "user_signing_validator_pack",
        _USER_PACK_TOML,
        _USER_IMPL_PY + "\n# tampered\n",
    )
    digest_a = compute_pack_digest(_discover_one(base_a))
    digest_b = compute_pack_digest(_discover_one(base_b))
    assert digest_a != digest_b


def test_pack_digest_trusted_membership() -> None:
    assert pack_digest_trusted("abc", frozenset({"abc", "def"})) is True
    assert pack_digest_trusted("xyz", frozenset({"abc", "def"})) is False
    assert pack_digest_trusted("abc", frozenset()) is False


def test_filter_off_is_byte_identical(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "packs"
    _write_pack(base, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    enabled = discover_pack_files([base])
    monkeypatch.delenv("MAGI_PACK_SIGNING_REQUIRED", raising=False)
    monkeypatch.delenv("MAGI_TRUSTED_PACK_DIGESTS", raising=False)

    # OFF: the same list object is returned untouched (no digest computed).
    assert filter_trusted_packs(enabled) is enabled


def test_filter_on_drops_untrusted_user_pack(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "packs"
    _write_pack(base, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    enabled = discover_pack_files([base])
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", "deadbeef")

    filtered = filter_trusted_packs(enabled)
    assert filtered == []


def test_filter_on_keeps_trusted_user_pack(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "packs"
    _write_pack(base, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    enabled = discover_pack_files([base])
    digest = compute_pack_digest(enabled[0])
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", f"other, {digest}")

    filtered = filter_trusted_packs(enabled)
    assert [p.manifest.pack_id for p in filtered] == ["user.signing-validator-pack"]


def test_filter_on_never_drops_bundled_first_party(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "packs"
    _write_pack(
        base,
        "openmagi_signing_bundled_pack",
        _BUNDLED_PACK_TOML,
        _BUNDLED_IMPL_PY,
    )
    enabled = discover_pack_files([base])
    # Signing required + an allowlist that does NOT contain the bundled digest.
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", "deadbeef")

    filtered = filter_trusted_packs(enabled)
    assert [p.manifest.pack_id for p in filtered] == ["openmagi.signing-bundled-pack"]


def test_load_into_registries_drops_untrusted_when_signing_required(
    tmp_path: Path, monkeypatch
) -> None:
    from magi_agent.packs.registries import load_into_registries

    base = tmp_path / "packs"
    _write_pack(base, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", "deadbeef")

    registries, report = load_into_registries([base])
    assert "validator:userSigned@1" not in report.registered
    assert registries.validators.list_refs() == ()


def test_load_into_registries_keeps_trusted_when_signing_required(
    tmp_path: Path, monkeypatch
) -> None:
    from magi_agent.packs.registries import load_into_registries

    base = tmp_path / "packs"
    _write_pack(base, "user_signing_validator_pack", _USER_PACK_TOML, _USER_IMPL_PY)
    digest = compute_pack_digest(_discover_one(base))
    monkeypatch.setenv("MAGI_PACK_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("MAGI_TRUSTED_PACK_DIGESTS", digest)

    registries, report = load_into_registries([base])
    assert "validator:userSigned@1" in report.registered
