"""J-10 — single source of truth for credential-register payload validation.

``credentials_admin/vault_server.py:_validate_body`` and
``transport/credentials.py:_validate_body`` carried two byte-identical
copies of the register-payload validator (``service``/``label``/
``auth_scheme``/``secret``/``requires_approval``/optional ``host``).
The vault_server copy's leading comment even said "Mirrors
transport.credentials' validation" — admitting the drift risk.

This module is the single home for the validator. The HTTP-shape
(``JSONResponse``) is left to each caller — the validator returns a
typed dataclass-shaped result so the wrapper can render its own
response. Both surfaces import the same function and pin to the same
``MAX_FIELD_LEN`` constant.

No behavior change: this is a pure dedup. The byte-for-byte field checks
move here; both call sites map the typed outcome onto their existing
``JSONResponse`` shapes (unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass


MAX_FIELD_LEN = 256


@dataclass(frozen=True)
class RegisterFields:
    """Validated register-payload fields, ready for the credential store."""

    service: str
    label: str
    auth_scheme: str
    secret: str
    requires_approval: bool
    host: str | None


@dataclass(frozen=True)
class RegisterPayloadError:
    """A typed validator failure carrying the error code and (optionally)
    the offending field name.

    HTTP framing (``JSONResponse``) is left to the caller — that way the
    validator stays framework-agnostic and the two surfaces keep their
    existing response shapes byte-identical to before.
    """

    error: str
    field: str | None = None


def validate_register_body(body: object) -> RegisterFields | RegisterPayloadError:
    """Validate a credential-register payload.

    Returns a populated :class:`RegisterFields` on success or a
    :class:`RegisterPayloadError` describing the first failed check.
    NEVER echoes the secret in an error (the secret field is checked
    for presence only — its value never lands in an error response).
    """

    if not isinstance(body, dict):
        return RegisterPayloadError(error="object_required")
    service = body.get("service")
    label = body.get("label")
    auth_scheme = body.get("auth_scheme")
    secret = body.get("secret")
    requires_approval_raw = body.get("requires_approval", False)
    if not isinstance(requires_approval_raw, bool):
        return RegisterPayloadError(
            error="field_invalid", field="requires_approval"
        )
    for name, value in (
        ("service", service),
        ("label", label),
        ("auth_scheme", auth_scheme),
        ("secret", secret),
    ):
        if not isinstance(value, str) or not value.strip():
            return RegisterPayloadError(error="field_required", field=name)
        if len(value) > MAX_FIELD_LEN and name != "secret":
            return RegisterPayloadError(error="field_too_long", field=name)
    # Optional non-secret target host for the local egress proxy. Validated
    # like the other string fields but allowed to be absent (→ None,
    # resolved from the service map at proxy time).
    host_raw = body.get("host")
    host: str | None = None
    if host_raw is not None:
        if not isinstance(host_raw, str) or not host_raw.strip():
            return RegisterPayloadError(error="field_invalid", field="host")
        if len(host_raw) > MAX_FIELD_LEN:
            return RegisterPayloadError(error="field_too_long", field="host")
        host = host_raw.strip()
    return RegisterFields(
        service=str(service).strip(),
        label=str(label).strip(),
        auth_scheme=str(auth_scheme).strip(),
        secret=str(secret),
        requires_approval=bool(requires_approval_raw),
        host=host,
    )


__all__ = [
    "MAX_FIELD_LEN",
    "RegisterFields",
    "RegisterPayloadError",
    "validate_register_body",
]
