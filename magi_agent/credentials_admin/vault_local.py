"""Local vault seam for the dashboard "Credentials" registration feature.

This mirrors the hosted Clawy "agent vault" C design: the dashboard collects a
plaintext secret, forwards it to a local vault seam, and persists ONLY redacted
metadata. The secret is dropped the instant the seam returns — it is NEVER
logged, NEVER included in an exception, NEVER part of a return value, and NEVER
written to durable storage.

Default-OFF: unless ``MAGI_VAULT_ADMIN_ENABLED`` is truthy the seam is inert and
``register_credential`` returns ``{"disabled": True}`` with no network call. When
enabled, a single transport function reads ``MAGI_VAULT_ADMIN_URL`` — this is the
slot where sub-project B's real per-bot vault admin API plugs in.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping

# I-1: the three vault master switches now route through the typed flag
# registry via ``flag_bool``; the URL string already routed through
# ``flag_str`` (I-4). The per-module ``_truthy`` shim is gone — every
# vault-env read sits on the typed-registry path.
from magi_agent.config.flags import flag_bool as _flag_bool
from magi_agent.config.flags import flag_str as _flag_str
from magi_agent.credentials_admin.local_vault import LocalVault
from magi_agent.storage.durable_store import DurableRecord

logger = logging.getLogger(__name__)

# Marker substrings (compacted, lowercased) that indicate a value may carry
# secret material. Used by the redaction helper as a defensive backstop; the
# secret is never deliberately passed to any sink, but a stray value should
# still be scrubbed rather than logged.
_REDACTED = "[redacted]"


def vault_admin_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return _flag_bool("MAGI_VAULT_ADMIN_ENABLED", env=env)


def _external_vault_url(env: Mapping[str, str]) -> str:
    return (_flag_str("MAGI_VAULT_ADMIN_URL", env=env) or "").strip()


def local_vault_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the native local vault backend is active.

    Default OFF at the helper level (code default stays conservative). The local
    serve / web-dashboard bootstrap is the ONLY place that flips this on by
    setdefault'ing ``MAGI_LOCAL_VAULT_ENABLED=1`` (see
    ``runtime/local_defaults.py``), so hosted bots — which never run that local
    overlay and which set ``MAGI_VAULT_ADMIN_URL`` once a hosted vault exists —
    stay on the disabled/pending path and never silently write secrets to a PVC.

    When an external ``MAGI_VAULT_ADMIN_URL`` is configured the external HTTP
    backend takes precedence and the local backend is NOT used, even if this flag
    is set.
    """
    env = os.environ if env is None else env
    if _external_vault_url(env):
        return False
    return _flag_bool("MAGI_LOCAL_VAULT_ENABLED", env=env)


def local_vault_proxy_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the local credential-injecting forward proxy should start.

    Gated identically to ``local_vault_enabled``: it requires the native local
    vault to be active AND the proxy flag set, and is forced OFF whenever an
    external ``MAGI_VAULT_ADMIN_URL`` is configured (hosted bots never run the
    local proxy). The serve bootstrap is the only place that setdefaults
    ``MAGI_LOCAL_VAULT_PROXY_ENABLED=1`` (see ``runtime/local_defaults.py``); the
    helper default stays OFF so library/test imports never start a proxy.
    """
    env = os.environ if env is None else env
    if _external_vault_url(env):
        return False
    if not local_vault_enabled(env):
        return False
    return _flag_bool("MAGI_LOCAL_VAULT_PROXY_ENABLED", env=env)


def _local_vault(env: Mapping[str, str] | None = None) -> LocalVault:
    return LocalVault()


def redact(value: object) -> str:
    """Return a digest-anchored, secret-free token for an arbitrary value.

    Never returns the input. For any value we emit ``[redacted]`` plus a short
    salted-free sha256 prefix so two distinct secrets are distinguishable in
    diagnostics without ever exposing plaintext. This is the ONLY function that
    is ever allowed to touch a secret on its way to a log line.
    """
    text = "" if value is None else str(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{_REDACTED} sha256:{digest}"


def _secret_fingerprint(secret: str) -> str:
    """A non-reversible fingerprint of the secret, safe to keep in memory only.

    NOT persisted, NOT logged. Used purely so the seam transport can prove it
    forwarded *something* without ever holding the plaintext beyond the call.
    """
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()


def register_credential(
    *,
    service: str,
    label: str,
    auth_scheme: str,
    secret: str,
    requires_approval: bool = False,
) -> dict[str, object]:
    """Forward a secret to the local vault and return a redacted result.

    Returns ``{"vault_ref": "<ref>"}`` when the vault accepted the secret, or
    ``{"disabled": True}`` when the seam is default-OFF. The ``secret`` argument
    is consumed locally and is never returned, logged, or raised.

    ``requires_approval`` marks the credential as guarded: the future vault
    raises a human-approval request (see ``resolve_approval``) before the agent
    may use it. It is forwarded to the vault seam body and is non-secret.

    Backend resolution (hosted behavior preserved):
      * ``MAGI_VAULT_ADMIN_URL`` set → external HTTP path (``_forward_to_vault``).
      * else local vault enabled → native encrypted ``LocalVault``.
      * else → disabled/pending.
    """
    env = os.environ
    use_external = vault_admin_enabled(env) and bool(_external_vault_url(env))
    use_local = (not use_external) and local_vault_enabled(env)

    if not use_external and not use_local:
        # Default-OFF: inert. No network, no vault_ref. The caller records the
        # credential metadata with a "pending" status.
        return {"disabled": True}

    try:
        if use_local:
            vault_ref = _local_vault(env).store_secret(secret)
        else:
            vault_ref = _forward_to_vault(
                service=service,
                label=label,
                auth_scheme=auth_scheme,
                secret=secret,
                requires_approval=requires_approval,
            )
    except Exception:  # noqa: BLE001 - normalize to a secret-free error
        # Re-raise a scrubbed error: the original exception may have embedded the
        # request/secret, so we never propagate it directly.
        raise VaultSeamError(
            f"vault registration failed for service={service!r} "
            f"(secret {redact(secret)})"
        ) from None
    finally:
        # Drop the local reference promptly; the plaintext must not outlive the
        # call frame.
        secret = ""  # noqa: F841 - intentional scrub

    return {"vault_ref": vault_ref}


def revoke_credential(*, vault_ref: str) -> None:
    """Revoke a previously issued vault reference. No-op when default-OFF.

    Routes to the same backend as registration: external HTTP when
    ``MAGI_VAULT_ADMIN_URL`` is set, else the native local vault's
    ``delete_secret`` when the local backend is enabled.
    """
    env = os.environ
    if vault_admin_enabled(env) and _external_vault_url(env):
        _revoke_in_vault(vault_ref=vault_ref)
        return
    if local_vault_enabled(env):
        _local_vault(env).delete_secret(vault_ref)
        return


def vault_status(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Report whether a vault backend is provisioned and healthy.

    Default-OFF → ``{"present": False, "healthy": False}`` (honest "not
    provisioned" state surfaced by the dashboard banner).
    """
    env = os.environ if env is None else env

    # External HTTP backend takes precedence when its URL is configured.
    if vault_admin_enabled(env) and _external_vault_url(env):
        # Without a live probe we cannot claim healthy; B's real adapter replaces
        # this with an actual health call. Honest: present-but-unprobed → not
        # healthy.
        return {"present": True, "healthy": False}

    # Native local vault: present + healthy when the dir is usable. This is what
    # flips the dashboard "Vault not provisioned" banner on the local serve path.
    if local_vault_enabled(env):
        provisioned = _local_vault(env).is_provisioned()
        return {"present": provisioned, "healthy": provisioned}

    # Disabled: honest "not provisioned".
    return {"present": False, "healthy": False}


class VaultSeamError(RuntimeError):
    """Secret-free error raised when the vault seam fails."""


def _forward_to_vault(
    *,
    service: str,
    label: str,
    auth_scheme: str,
    secret: str,
    requires_approval: bool = False,
) -> str:
    """Single transport function — the slot for B's real vault admin API.

    In this OSS scaffold there is no real backend wired, so even when the flag is
    ON this raises a secret-free error unless ``MAGI_VAULT_ADMIN_URL`` is set.
    The fingerprint proves a secret was handled without exposing it.

    ``requires_approval`` belongs in the (non-secret) vault request body so B's
    real adapter can register the credential as guarded.
    """
    url = (_flag_str("MAGI_VAULT_ADMIN_URL") or "").strip()
    if not url:
        raise VaultSeamError(
            "MAGI_VAULT_ADMIN_ENABLED set but MAGI_VAULT_ADMIN_URL missing"
        )
    # NOTE: real HTTP forwarding lands here in sub-project B. We compute a
    # fingerprint (never the plaintext) so the transport contract is exercised.
    _ = _secret_fingerprint(secret)
    raise VaultSeamError(
        "vault admin transport is not wired in the OSS scaffold; "
        f"service={service!r} scheme={auth_scheme!r} "
        f"requires_approval={requires_approval!r}"
    )


def resolve_approval(*, approval_id: str, decision: str) -> dict[str, object]:
    """Forward an operator's approval decision to the vault. No-op when OFF.

    Returns ``{"disabled": True}`` when the seam is default-OFF (the caller has
    already recorded the decision in the local approvals store, which is the
    source of truth for the dashboard). When enabled this is the slot where B's
    real vault admin API is told the guarded credential may (or may not) be used.
    """
    if not vault_admin_enabled():
        return {"disabled": True}
    return _resolve_in_vault(approval_id=approval_id, decision=decision)


def _resolve_in_vault(*, approval_id: str, decision: str) -> dict[str, object]:
    url = (_flag_str("MAGI_VAULT_ADMIN_URL") or "").strip()
    if not url:
        # Enabled-but-unwired: honest no-op so the local decision still stands.
        return {"disabled": True}
    # Real resolve transport lands here in sub-project B.
    return {"resolved": True, "approval_id": approval_id, "decision": decision}


def _revoke_in_vault(*, vault_ref: str) -> None:
    url = (_flag_str("MAGI_VAULT_ADMIN_URL") or "").strip()
    if not url:
        return
    # Real revoke transport lands here in sub-project B.
    return


def _metadata_digest(*, service: str, auth_scheme: str, status: str) -> str:
    payload = f"cred:{service}:{auth_scheme}:{status}"
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_durable_metadata_record(
    *,
    credential_id: str,
    service: str,
    label: str,
    auth_scheme: str,
    status: str,
    vault_ref: str | None,
) -> DurableRecord:
    """Build the digest-anchored durable index record for a credential.

    The record carries ONLY digests/safe refs — never the plaintext secret and
    never the free-text label (the durable store's regex guards would reject any
    secret-shaped value, and we do not bypass them). Human-readable fields live
    only in the local redacted-metadata JSON store.
    """
    content_digest = _metadata_digest(
        service=service, auth_scheme=auth_scheme, status=status
    )
    metadata: dict[str, object] = {"vaultRefPresent": vault_ref is not None}
    return DurableRecord(
        collection="credential_lease_metadata",
        recordId="cred-meta:" + credential_id,
        contentDigest=content_digest,
        policySnapshotDigest=content_digest,
        metadata=metadata,
    )
