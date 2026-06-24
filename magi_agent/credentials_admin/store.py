"""Local redacted-metadata store for registered credentials.

Mirrors ``magi_agent/customize/store.py``: an atomically-written JSON file beside
the runtime config. It holds ONLY non-secret metadata — service, label,
auth_scheme, status, vault_ref, created_at — for the dashboard list. The
plaintext secret NEVER reaches this file; it is forwarded to the vault seam and
dropped before persistence.

In addition to this listable projection, the route layer sends a digest-anchored
record through ``DurableRecord`` validation (see
``vault_local.build_durable_metadata_record``) so the durable store's
secret-shaped-value guards are exercised, not bypassed.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Status values for a registered credential.
#   pending  — metadata recorded for a future vault workflow (no vault_ref)
#   active   — vault accepted the secret and returned a vault_ref
#   revoked  — operator revoked the credential
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

DEFAULT_DATA: dict[str, Any] = {"credentials": []}


def credentials_path() -> Path:
    """Locate credentials.json beside the runtime config (env-overridable)."""
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    override = flag_str("MAGI_CREDENTIALS") or None
    if override:
        return Path(override)
    config = flag_str("MAGI_CONFIG") or None
    if config:
        return Path(config).parent / "credentials.json"
    return Path.home() / ".magi" / "credentials.json"


def _clone_default() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_DATA)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    credentials = data.get("credentials")
    if not isinstance(credentials, list):
        return _clone_default()
    cleaned: list[dict[str, Any]] = []
    for item in credentials:
        if not isinstance(item, dict):
            continue
        cleaned.append(public_metadata(item))
    return {"credentials": cleaned}


def public_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored credential record into a strictly non-secret projection."""
    return {
        "id": str(item.get("id", "")),
        "service": str(item.get("service", "")),
        "label": str(item.get("label", "")),
        "auth_scheme": str(item.get("auth_scheme", "")),
        "status": str(item.get("status", STATUS_PENDING)),
        "vault_ref": item.get("vault_ref") if item.get("vault_ref") else None,
        "requires_approval": bool(item.get("requires_approval", False)),
        # Additive, non-secret target host for the local egress proxy to match a
        # request against this credential. Optional; None when unset.
        "host": str(item["host"]) if item.get("host") else None,
        "created_at": str(item.get("created_at", "")),
    }


def load_credentials(path: Path | None = None) -> dict[str, Any]:
    """Load + shape-normalize the file. Never raises; falls back to empty."""
    target = path or credentials_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, OSError):
        return _clone_default()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _clone_default()
    if not isinstance(data, dict):
        return _clone_default()
    return _normalize(data)


def save_credentials(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the credentials file (normalized). Creates parent dirs."""
    target = path or credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize(data if isinstance(data, dict) else {})
    payload = json.dumps(normalized, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def add_credential(
    *,
    service: str,
    label: str,
    auth_scheme: str,
    status: str,
    vault_ref: str | None,
    requires_approval: bool = False,
    host: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append one credential's metadata, save atomically, return its projection."""
    target = path or credentials_path()
    data = load_credentials(target)
    record = {
        "id": uuid.uuid4().hex,
        "service": service,
        "label": label,
        "auth_scheme": auth_scheme,
        "status": status,
        "vault_ref": vault_ref,
        "requires_approval": requires_approval,
        "host": host,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    projection = public_metadata(record)
    data["credentials"].append(projection)
    save_credentials(data, target)
    return projection


def set_status(
    credential_id: str,
    status: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Update one credential's status; return its projection or None if absent."""
    target = path or credentials_path()
    data = load_credentials(target)
    updated: dict[str, Any] | None = None
    for item in data["credentials"]:
        if item.get("id") == credential_id:
            item["status"] = status
            updated = public_metadata(item)
            break
    if updated is None:
        return None
    save_credentials(data, target)
    return updated


def set_requires_approval_by_ref(
    vault_ref: str,
    requires_approval: bool,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Flip a credential's ``requires_approval`` flag, located by its opaque
    ``vault_ref``; return its redacted projection or None if absent.

    No secret is involved: ``requires_approval`` is non-secret metadata. Keyed by
    vault_ref (not id) because that is the opaque handle the control plane holds.
    """
    target = path or credentials_path()
    data = load_credentials(target)
    updated: dict[str, Any] | None = None
    for item in data["credentials"]:
        if item.get("vault_ref") == vault_ref:
            item["requires_approval"] = bool(requires_approval)
            updated = public_metadata(item)
            break
    if updated is None:
        return None
    save_credentials(data, target)
    return updated
