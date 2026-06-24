"""Credential-use approval resolver seam (in-chat approval, phase 1).

The tool-permission layer asks this seam, BEFORE a tool egresses, whether the
call's target host maps to an Agent Vault credential marked "require approval",
and whether a current grant already lets it through. On a user "allow" the seam
records a grant so the egress proxy (the hard enforcement backstop) injects.

Two implementations share this protocol:

* :class:`LocalCredentialApprovalResolver` -- reads/writes the local redacted
  metadata + approvals stores directly (OSS local serve, ``~/.magi``).
* :class:`NullCredentialApprovalResolver` -- the inert default (no vault): never
  gates, always "granted".

A hosted implementation (talking to the same-pod sidecar admin API) lands in a
later phase behind the same protocol.

Host matching reuses :func:`local_proxy_decision.resolve_host` so the tool-layer
check and the egress proxy can never disagree about what host a credential
guards. NO secret or opaque vault_ref is ever returned by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from magi_agent.credentials_admin import approvals_store, store
from magi_agent.credentials_admin.local_proxy_decision import resolve_host


@dataclass(frozen=True)
class CredentialApprovalNeeded:
    """A redacted descriptor of the credential guarding an outbound host.

    Carries only non-secret metadata so it is safe to surface in a control
    request shown to the user. NEVER carries the secret or the opaque vault_ref.
    """

    credential_id: str
    service: str
    label: str
    host: str


class CredentialApprovalResolver(Protocol):
    def needs_approval(self, host: str) -> CredentialApprovalNeeded | None: ...

    def is_granted(self, credential_id: str) -> bool: ...

    def grant(self, credential_id: str, *, persistent: bool) -> None: ...


# Tool arguments that may carry an outbound URL/host, in priority order. Bash is
# intentionally NOT covered: a shell command's host is fuzzy to extract, and Bash
# already prompts as a dangerous tool; the egress proxy remains the backstop.
_HOST_ARG_KEYS = ("url", "uri", "endpoint", "target", "host")


def extract_egress_host(tool_name: str, arguments: dict[str, object]) -> str | None:
    """Best-effort target host for an outbound tool call, lowercased, no port.

    Returns None when no URL/host-like argument is present. Never raises.
    """
    del tool_name  # reserved for future per-tool extraction rules
    if not isinstance(arguments, dict):
        return None
    for key in _HOST_ARG_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = raw.strip()
        try:
            # Accept full URLs and bare hosts. urlsplit needs a scheme to populate
            # hostname, so prepend one when the value has no scheme.
            candidate = value if "//" in value else f"//{value}"
            host = urlsplit(candidate, scheme="https").hostname
        except ValueError:
            host = None
        if host:
            return host.strip().lower()
    return None


class NullCredentialApprovalResolver:
    """Inert resolver: no vault, nothing to gate. Used when the seam is off."""

    def needs_approval(self, host: str) -> CredentialApprovalNeeded | None:
        del host
        return None

    def is_granted(self, credential_id: str) -> bool:
        del credential_id
        return True

    def grant(self, credential_id: str, *, persistent: bool) -> None:
        del credential_id, persistent


class LocalCredentialApprovalResolver:
    """Resolver backed by the local redacted-metadata + approvals stores.

    ``credentials_path`` / ``approvals_path`` default to the canonical local
    locations (``~/.magi`` via the stores' own resolution); the hosted sidecar
    passes explicit paths. Reads only non-secret metadata; never decrypts.
    """

    def __init__(
        self,
        *,
        credentials_path: Path | None = None,
        approvals_path: Path | None = None,
    ) -> None:
        self._credentials_path = credentials_path
        self._approvals_path = approvals_path

    def needs_approval(self, host: str) -> CredentialApprovalNeeded | None:
        target = (host or "").strip().lower()
        if not target:
            return None
        try:
            records = store.load_credentials(self._credentials_path)["credentials"]
        except Exception:  # noqa: BLE001 - a metadata read must not crash a turn
            return None
        for cred in records:
            if str(cred.get("status")) != store.STATUS_ACTIVE:
                continue
            if not cred.get("requires_approval"):
                continue
            resolved = resolve_host(cred)
            if resolved is not None and resolved == target:
                return CredentialApprovalNeeded(
                    credential_id=str(cred.get("id", "")),
                    service=str(cred.get("service", "")),
                    label=str(cred.get("label", "")),
                    host=target,
                )
        return None

    def is_granted(self, credential_id: str) -> bool:
        if not credential_id:
            return False
        try:
            granted = approvals_store.list_approvals(
                status=approvals_store.STATUS_APPROVED, path=self._approvals_path
            )
        except Exception:  # noqa: BLE001 - fail closed: treat as not granted
            return False
        return any(a.get("credential_id") == credential_id for a in granted)

    def grant(self, credential_id: str, *, persistent: bool) -> None:
        """Record an approved grant the egress proxy will honor.

        ``persistent`` is threaded for a later grant-scope phase (per-turn vs
        remember); phase 1 writes a standard approved row either way, matching the
        proxy's current approve-once semantics.
        """
        del persistent
        if not credential_id:
            return
        created = approvals_store.add_approval(
            credential_id=credential_id,
            requested_action="egress_credential_use",
            target_host="",
            reason="approved in chat",
            path=self._approvals_path,
        )
        approvals_store.decide_approval(
            str(created.get("id", "")),
            approvals_store.STATUS_APPROVED,
            path=self._approvals_path,
        )
