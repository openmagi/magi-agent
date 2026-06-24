"""Local approval-request store for guarded credentials.

Sibling of ``store.py``. Where ``store.py`` tracks registered credential
metadata, this file tracks human-approval *requests*: when the (future) vault
sees the agent reach for a credential marked ``requires_approval``, it enqueues
a request here and the local operator approves or denies it from the dashboard.

Like the credential store this is metadata ONLY — there is never a secret in an
approval record. The file is written atomically beside the runtime config.

Record shape (all non-secret):
  {id, credential_id, requested_action, target_host,
   status(pending|approved|denied|expired), reason, created_at, decided_at}
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Status values for an approval request.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"

VALID_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED, STATUS_EXPIRED}
)

# A decision is one of the two terminal operator verdicts.
DECISION_STATUSES = frozenset({STATUS_APPROVED, STATUS_DENIED})

DEFAULT_DATA: dict[str, Any] = {"approvals": []}

_REDACTED = "[redacted]"
_SECRET_TEXT_RE = re.compile(
    r"authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|"
    r"(?:password|api[_-]?key|auth[_-]?key|session[_-]?key|private[_-]?key|"
    r"connector[_-]?token|secret|credential|token|signature)\s*[:=]|"
    r"x-amz-signature|x-goog-signature|sig=|signed[_-]?url|"
    r"\bsk-[A-Za-z0-9._-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|AKIA[0-9A-Z]{8,}",
    re.IGNORECASE,
)


def approvals_path() -> Path:
    """Locate approvals.json beside the runtime config (env-overridable)."""
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    override = flag_str("MAGI_CREDENTIAL_APPROVALS") or None
    if override:
        return Path(override)
    config = flag_str("MAGI_CONFIG") or None
    if config:
        return Path(config).parent / "credential_approvals.json"
    return Path.home() / ".magi" / "credential_approvals.json"


def _clone_default() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_DATA)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def public_approval(item: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored approval into a strictly non-secret projection.

    Drops any unknown keys so a stray secret-bearing field can never survive
    a round-trip through the store.
    """
    status = str(item.get("status", STATUS_PENDING))
    if status not in VALID_STATUSES:
        status = STATUS_PENDING
    decided_at = item.get("decided_at")
    reason = str(item.get("reason", ""))
    granted_until = item.get("granted_until")
    return {
        "id": str(item.get("id", "")),
        "credential_id": str(item.get("credential_id", "")),
        "requested_action": str(item.get("requested_action", "")),
        "target_host": str(item.get("target_host", "")),
        "status": status,
        "reason": _safe_reason(reason),
        "created_at": str(item.get("created_at", "")),
        "decided_at": str(decided_at) if decided_at else None,
        # Optional grant expiry (ISO-8601 UTC). None = no expiry (persistent /
        # not an in-chat grant). Non-secret. Kept through the normalize round-trip
        # so the egress proxy can honor it.
        "granted_until": str(granted_until) if granted_until else None,
    }


def grant_is_active(approval: dict[str, Any], *, now: str | None = None) -> bool:
    """Whether an APPROVED approval currently grants use (expiry-aware).

    True when status is ``approved`` AND it is non-expiring (``granted_until`` is
    None) OR its expiry is still in the future. ``granted_until`` and ``_now()``
    share one ISO-8601 UTC format, so a lexicographic compare is chronological.
    """
    if str(approval.get("status")) != STATUS_APPROVED:
        return False
    granted_until = approval.get("granted_until")
    if not granted_until:
        return True
    return str(granted_until) > (now or _now())


def _safe_reason(value: str) -> str:
    return _REDACTED if _SECRET_TEXT_RE.search(value) else value


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    approvals = data.get("approvals")
    if not isinstance(approvals, list):
        return _clone_default()
    cleaned: list[dict[str, Any]] = []
    for item in approvals:
        if not isinstance(item, dict):
            continue
        cleaned.append(public_approval(item))
    return {"approvals": cleaned}


def load_approvals(path: Path | None = None) -> dict[str, Any]:
    """Load + shape-normalize the file. Never raises; falls back to empty."""
    target = path or approvals_path()
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


def save_approvals(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the approvals file (normalized). Creates parent dirs."""
    target = path or approvals_path()
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


def add_approval(
    *,
    credential_id: str,
    requested_action: str,
    target_host: str,
    reason: str = "",
    granted_until: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append one pending approval request, save atomically, return projection."""
    target = path or approvals_path()
    data = load_approvals(target)
    record = {
        "id": uuid.uuid4().hex,
        "credential_id": credential_id,
        "requested_action": requested_action,
        "target_host": target_host,
        "status": STATUS_PENDING,
        "reason": reason,
        "created_at": _now(),
        "decided_at": None,
        "granted_until": granted_until,
    }
    projection = public_approval(record)
    data["approvals"].append(projection)
    save_approvals(data, target)
    return projection


def list_approvals(
    *,
    status: str | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return all approvals, optionally filtered by status."""
    data = load_approvals(path)
    approvals = data["approvals"]
    if status is None:
        return approvals
    return [a for a in approvals if a.get("status") == status]


def get_approval(
    approval_id: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Return one approval's projection or None if absent."""
    for item in load_approvals(path)["approvals"]:
        if item.get("id") == approval_id:
            return item
    return None


def decide_approval(
    approval_id: str,
    decision: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Record a terminal decision + decided_at; return projection or None.

    ``decision`` must be one of ``DECISION_STATUSES`` — the caller is expected to
    validate it first; an invalid value is rejected here as a guard.
    """
    if decision not in DECISION_STATUSES:
        raise ValueError("decision must be 'approved' or 'denied'")
    target = path or approvals_path()
    data = load_approvals(target)
    updated: dict[str, Any] | None = None
    for item in data["approvals"]:
        if item.get("id") == approval_id:
            item["status"] = decision
            item["decided_at"] = _now()
            updated = public_approval(item)
            break
    if updated is None:
        return None
    save_approvals(data, target)
    return updated
