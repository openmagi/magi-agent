"""Tests for the native encrypted local vault backend (``LocalVault``).

Security-critical invariants under test:
  * the plaintext secret is encrypted at rest — it never appears verbatim in
    ``secrets.enc``;
  * the key file is mode 0600 and the vault dir is 0700;
  * the plaintext secret is never logged on store/error paths;
  * ``get_secret`` round-trips the exact plaintext (the one decryption path);
  * delete is idempotent.
"""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path

import pytest

from magi_agent.credentials_admin import local_vault

# A realistic, "secret-shaped" value so at-rest ciphertext assertions are real.
SECRET_VALUE = "sk-live-abcd1234EFGH5678ijkl9012MNOP3456"


def _vault(tmp_path: Path) -> local_vault.LocalVault:
    return local_vault.LocalVault(vault_dir=tmp_path / "vault")


def test_is_provisioned_true_when_dir_usable(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert vault.is_provisioned() is True
    # The key file is materialized on first use and is 0600.
    key_path = vault.vault_dir / "vault.key"
    assert key_path.is_file()
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_vault_dir_is_0700(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.is_provisioned()
    mode = stat.S_IMODE(vault.vault_dir.stat().st_mode)
    assert mode == 0o700, oct(mode)


def test_store_secret_returns_ref_and_round_trips(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ref = vault.store_secret(SECRET_VALUE)
    assert isinstance(ref, str) and ref
    # The one decryption path returns the EXACT plaintext.
    assert vault.get_secret(ref) == SECRET_VALUE


def test_ciphertext_at_rest_is_not_plaintext(tmp_path: Path) -> None:
    """SECURITY: the secret is encrypted at rest — plaintext absent from disk."""
    vault = _vault(tmp_path)
    ref = vault.store_secret(SECRET_VALUE)
    enc_path = vault.vault_dir / "secrets.enc"
    assert enc_path.is_file()
    raw = enc_path.read_bytes()
    # The plaintext secret bytes must NOT appear anywhere in the on-disk file.
    assert SECRET_VALUE.encode("utf-8") not in raw
    # The file is a JSON map of {ref: ciphertext}; the ref is present, the
    # plaintext is not.
    parsed = json.loads(raw.decode("utf-8"))
    assert ref in parsed
    assert SECRET_VALUE not in json.dumps(parsed)
    # Encrypted store is 0600.
    mode = stat.S_IMODE(enc_path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_get_secret_unknown_ref_returns_none(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert vault.get_secret("does-not-exist") is None


def test_delete_secret_removes_and_is_idempotent(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    ref = vault.store_secret(SECRET_VALUE)
    assert vault.get_secret(ref) == SECRET_VALUE
    vault.delete_secret(ref)
    assert vault.get_secret(ref) is None
    # Idempotent: deleting again does not raise.
    vault.delete_secret(ref)
    assert vault.get_secret(ref) is None


def test_store_secret_never_logs_plaintext_on_success(tmp_path, caplog) -> None:
    """SECURITY: the plaintext is never logged on the happy path."""
    vault = _vault(tmp_path)
    with caplog.at_level(logging.DEBUG):
        vault.store_secret(SECRET_VALUE)
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage()
        assert SECRET_VALUE not in str(record.args)


def test_store_secret_never_logs_plaintext_on_error(tmp_path, caplog, monkeypatch) -> None:
    """SECURITY: a persistence failure must not leak the secret into logs."""
    vault = _vault(tmp_path)
    # Force the atomic write to fail so the error path runs.
    monkeypatch.setattr(
        local_vault,
        "_atomic_write_bytes",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(local_vault.LocalVaultError):
            vault.store_secret(SECRET_VALUE)
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage()
        assert SECRET_VALUE not in str(record.args)


def test_error_message_contains_no_secret(tmp_path, monkeypatch) -> None:
    """SECURITY: the raised exception text never embeds the plaintext."""
    vault = _vault(tmp_path)
    monkeypatch.setattr(
        local_vault,
        "_atomic_write_bytes",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(local_vault.LocalVaultError) as exc_info:
        vault.store_secret(SECRET_VALUE)
    assert SECRET_VALUE not in str(exc_info.value)


def test_key_is_stable_across_instances(tmp_path: Path) -> None:
    """A second LocalVault over the same dir decrypts the first one's secrets."""
    vault_a = _vault(tmp_path)
    ref = vault_a.store_secret(SECRET_VALUE)
    vault_b = _vault(tmp_path)
    assert vault_b.get_secret(ref) == SECRET_VALUE


def test_resolve_vault_dir_env_override(monkeypatch, tmp_path) -> None:
    target = tmp_path / "explicit-vault"
    monkeypatch.setenv("MAGI_VAULT_DIR", str(target))
    monkeypatch.delenv("MAGI_CONFIG", raising=False)
    assert local_vault.resolve_vault_dir() == target


def test_resolve_vault_dir_beside_config(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MAGI_VAULT_DIR", raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    assert local_vault.resolve_vault_dir() == tmp_path / "vault"


def test_resolve_vault_dir_default_home(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_VAULT_DIR", raising=False)
    monkeypatch.delenv("MAGI_CONFIG", raising=False)
    assert local_vault.resolve_vault_dir() == Path.home() / ".magi" / "vault"
