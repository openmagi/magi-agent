"""Native encrypted local vault backend for the dashboard "Credentials" feature.

This is the Phase-1 storage backend that makes the local dashboard's credential
registration *real*: instead of forwarding to an external vault URL (or returning
"not provisioned"), it encrypts the plaintext secret at rest with
``cryptography.fernet.Fernet`` and persists only ciphertext on disk.

Security model
--------------
* The plaintext secret is encrypted before it touches disk. The on-disk store
  (``secrets.enc``) holds ONLY Fernet ciphertext — never the plaintext.
* The Fernet key (``vault.key``) is generated on first use and written 0600. The
  vault dir is 0700. Neither the key nor any plaintext secret is ever logged,
  returned by an HTTP route, or embedded in an exception.
* ``get_secret`` is the single decryption path and is INTERNAL ONLY — see its
  docstring. It exists for the Phase-2 local egress proxy and must NEVER be wired
  to any dashboard / HTTP route.

This module deliberately does not hand-roll crypto and adds no new dependency:
``cryptography`` is already resolved in the runtime environment.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEY_FILENAME = "vault.key"
_STORE_FILENAME = "secrets.enc"


class LocalVaultError(RuntimeError):
    """Secret-free error raised when the local vault cannot persist a secret.

    The plaintext secret and the Fernet key are NEVER embedded in this error.
    """


def redact(value: object) -> str:
    """Return a secret-free placeholder for an arbitrary value.

    Never returns the input. Used as a defensive backstop so a stray value
    accidentally routed to a log line is scrubbed rather than exposed. We do not
    even emit a digest here (a digest of a low-entropy secret can be brute
    forced); a constant placeholder is the safe choice for a value that should
    never have reached a log in the first place.
    """
    return "[redacted]"


def resolve_vault_dir(env: Mapping[str, str] | None = None) -> Path:
    """Locate the vault directory, mirroring ``store.credentials_path()``.

    Resolution order:
      1. ``MAGI_VAULT_DIR`` env override.
      2. ``<MAGI_CONFIG parent>/vault`` when ``MAGI_CONFIG`` is set.
      3. ``~/.magi/vault``.
    """
    # I-1: route both knobs through the typed flag registry.
    # ``flag_str`` returns "" for unset (matching the registered
    # default); the truthy ``if override:`` / ``if config:`` checks
    # already gate on non-empty strings so the empty default is
    # byte-identical to the prior ``env.get(...)`` → None chain.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    env = os.environ if env is None else env
    override = flag_str("MAGI_VAULT_DIR", env=env)
    if override:
        return Path(override)
    config = flag_str("MAGI_CONFIG", env=env)
    if config:
        return Path(config).parent / "vault"
    return Path.home() / ".magi" / "vault"


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int) -> None:
    """Write ``data`` to ``path`` atomically (temp + os.replace) with ``mode``.

    The temp file is created in the destination directory so ``os.replace`` is
    atomic on the same filesystem. The file mode is enforced before the rename
    so the destination is never momentarily world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class LocalVault:
    """Encrypted-at-rest local secret store keyed by an opaque ``vault_ref``."""

    def __init__(self, *, vault_dir: Path | None = None) -> None:
        self.vault_dir = Path(vault_dir) if vault_dir is not None else resolve_vault_dir()

    @property
    def key_path(self) -> Path:
        return self.vault_dir / _KEY_FILENAME

    @property
    def store_path(self) -> Path:
        return self.vault_dir / _STORE_FILENAME

    # -- provisioning ---------------------------------------------------------

    def is_provisioned(self) -> bool:
        """True when the vault dir is usable (key readable or creatable).

        This drives ``vault_status.present``. It is intentionally side-effecting
        on success (it materializes the key on first use) so that "provisioned"
        means "ready to accept a secret right now".
        """
        try:
            self._load_or_create_key()
        except OSError:
            logger.warning("local vault not provisionable at %s", self.vault_dir)
            return False
        return True

    # -- public store API -----------------------------------------------------

    def store_secret(self, secret: str) -> str:
        """Encrypt ``secret`` at rest and return an opaque ``vault_ref``.

        The plaintext is consumed within this call: it is encrypted, persisted as
        ciphertext, and never logged, returned, or kept beyond the call frame.
        Raises ``LocalVaultError`` (secret-free) on a persistence failure.
        """
        ref = uuid.uuid4().hex
        try:
            fernet = Fernet(self._load_or_create_key())
            token = fernet.encrypt(secret.encode("utf-8"))
            store = self._load_store()
            store[ref] = token.decode("ascii")
            self._save_store(store)
        except LocalVaultError:
            raise
        except Exception:  # noqa: BLE001 - normalize to a secret-free error
            # The original exception may carry buffers/state derived from the
            # plaintext, so we never propagate it directly.
            logger.warning(
                "local vault store failed (secret %s)", redact(secret)
            )
            raise LocalVaultError("local vault failed to store the secret") from None
        finally:
            secret = ""  # noqa: F841 - intentional scrub; do not outlive the call
        return ref

    def delete_secret(self, vault_ref: str) -> None:
        """Remove the entry for ``vault_ref``. Idempotent (missing ref = no-op)."""
        try:
            store = self._load_store()
            if vault_ref in store:
                del store[vault_ref]
                self._save_store(store)
        except Exception:  # noqa: BLE001 - never leak; delete is best-effort
            logger.warning("local vault delete failed for ref")
            return

    def get_secret(self, vault_ref: str) -> str | None:
        """Decrypt and return the plaintext for ``vault_ref`` (or None).

        ⚠️ INTERNAL DECRYPTION PATH — DO NOT EXPOSE OVER HTTP. ⚠️

        This is the ONLY function in the codebase that converts a stored
        ciphertext back into plaintext. It exists solely for the Phase-2 local
        forward proxy that injects credentials into bot egress. It must NEVER be
        wired to a dashboard API route, returned in an HTTP response, or logged.
        The dashboard only ever sees redacted metadata + the opaque ``vault_ref``.
        """
        try:
            store = self._load_store()
            token = store.get(vault_ref)
            if token is None:
                return None
            fernet = Fernet(self._load_or_create_key())
            return fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, OSError, ValueError):
            logger.warning("local vault decrypt failed for ref")
            return None

    # -- internals ------------------------------------------------------------

    def _load_or_create_key(self) -> bytes:
        """Return the Fernet key, generating + persisting it 0600 on first use."""
        self.vault_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir's mode is subject to umask; enforce 0700 explicitly.
        try:
            os.chmod(self.vault_dir, 0o700)
        except OSError:
            pass
        key_path = self.key_path
        if key_path.is_file():
            # Enforce 0600 on a pre-existing key (it may have been created with
            # looser perms outside this code path).
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
            return key_path.read_bytes()
        key = Fernet.generate_key()
        _atomic_write_bytes(key_path, key, mode=0o600)
        return key

    def _load_store(self) -> dict[str, str]:
        try:
            raw = self.store_path.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}

    def _save_store(self, store: Mapping[str, str]) -> None:
        payload = json.dumps(dict(store), sort_keys=True).encode("utf-8")
        _atomic_write_bytes(self.store_path, payload, mode=0o600)
