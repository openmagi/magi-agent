"""Pure decision core for the local credential-injecting forward proxy.

This module contains NO mitmproxy import and NO secret material. It answers a
single question for each outbound request the local proxy sees:

  Given the request host + the list of registered (redacted) credential metadata
  + an approvals lookup, what should the proxy DO?

The answer is one of three plans:

  * :class:`PassThrough`           — no matching active credential; forward as-is.
  * :class:`BlockPendingApproval`  — a matching credential requires approval and
                                     none is currently granted; the proxy should
                                     return a 403 and enqueue an approval request.
  * :class:`Inject`                — inject the auth header. The decision carries
                                     ONLY the ``vault_ref`` + a header *plan*
                                     (``header_name`` + ``value_prefix``). The
                                     plaintext secret is fetched separately inside
                                     the addon and NEVER enters this object.

Keeping this logic pure makes it fully unit-testable without mitmproxy and makes
it structurally impossible for a secret to leak through the decision layer: there
is no field here that could hold one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Built-in service → host map. Resolution order in ``resolve_host``: an explicit
# per-credential ``host`` wins, else this map by ``service``, else None (the
# request is not matched and passes through untouched).
#
# This is intentionally a small, sane starter set. It is extensible: an operator
# who needs another service either sets the credential's explicit ``host`` field
# or this map gains an entry in a follow-up. We do NOT guess hosts heuristically.
SERVICE_HOST_MAP: dict[str, str] = {
    "slack": "api.slack.com",
    "notion": "api.notion.com",
    "stripe": "api.stripe.com",
    "google": "www.googleapis.com",
    "github": "api.github.com",
}


@dataclass(frozen=True)
class PassThrough:
    """No matching active credential — forward the request unchanged."""


@dataclass(frozen=True)
class BlockPendingApproval:
    """A matching credential requires approval and none is granted.

    The proxy should respond 403 and enqueue an approval request. Carries only
    the (non-secret) credential id.
    """

    credential_id: str


@dataclass(frozen=True)
class Inject:
    """Inject the auth header.

    Carries ONLY the opaque ``vault_ref`` and the header plan. The plaintext
    secret is fetched (inside the addon) via ``LocalVault.get_secret(vault_ref)``
    and applied as ``header_name: value_prefix + secret``. There is deliberately
    no field on this object that could ever hold the secret.
    """

    vault_ref: str
    auth_scheme: str
    header_name: str
    value_prefix: str
    credential_id: str


InjectionDecision = PassThrough | BlockPendingApproval | Inject


def resolve_host(cred: dict[str, object]) -> str | None:
    """Resolve the target host for a credential.

    Order: explicit ``host`` wins, else ``SERVICE_HOST_MAP`` by ``service``,
    else None (no match → request passes through).
    """
    explicit = cred.get("host")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    service = cred.get("service")
    if isinstance(service, str):
        mapped = SERVICE_HOST_MAP.get(service.strip().lower())
        if mapped:
            return mapped
    return None


def _header_plan(cred: dict[str, object]) -> tuple[str, str]:
    """Return ``(header_name, value_prefix)`` for a credential's auth scheme.

    bearer  → ("Authorization", "Bearer ")
    basic   → ("Authorization", "Basic ")
    api_key → (cred["header_name"] or "Authorization", "")  # no prefix

    Unknown schemes fall back to the api_key shape (header as-is, no prefix) so
    an unexpected value never silently mangles the secret with a wrong prefix.
    """
    scheme = str(cred.get("auth_scheme", "")).strip().lower()
    if scheme == "bearer":
        return "Authorization", "Bearer "
    if scheme == "basic":
        return "Authorization", "Basic "
    # api_key (and any unknown scheme): inject raw value into the named header.
    header_name = cred.get("header_name")
    if isinstance(header_name, str) and header_name.strip():
        return header_name.strip(), ""
    return "Authorization", ""


def decide_injection(
    *,
    host: str,
    credentials: Sequence[dict[str, object]],
    approvals_lookup: Callable[[str], bool],
) -> InjectionDecision:
    """Decide what the proxy should do for a request to ``host``.

    Matches the FIRST ``status == 'active'`` credential whose resolved host equals
    the request host (case-insensitive). If that credential ``requires_approval``
    and ``approvals_lookup(credential_id)`` is falsey → ``BlockPendingApproval``.
    Otherwise → ``Inject`` carrying only the vault_ref + header plan.

    ``approvals_lookup(credential_id) -> bool`` returns True when a current
    approval grants use of the credential. The lookup is injected so this core
    stays pure (the addon wires it to ``approvals_store``).
    """
    target = (host or "").strip().lower()
    if not target:
        return PassThrough()

    for cred in credentials:
        if str(cred.get("status", "")).strip().lower() != "active":
            continue
        resolved = resolve_host(cred)
        if resolved is None or resolved != target:
            continue
        vault_ref = cred.get("vault_ref")
        if not isinstance(vault_ref, str) or not vault_ref:
            # Active-but-unreferenced credential cannot be injected; treat as a
            # non-match rather than guessing.
            continue
        credential_id = str(cred.get("id", ""))
        if bool(cred.get("requires_approval", False)) and not approvals_lookup(
            credential_id
        ):
            return BlockPendingApproval(credential_id=credential_id)
        header_name, value_prefix = _header_plan(cred)
        return Inject(
            vault_ref=vault_ref,
            auth_scheme=str(cred.get("auth_scheme", "")).strip().lower(),
            header_name=header_name,
            value_prefix=value_prefix,
            credential_id=credential_id,
        )

    return PassThrough()
