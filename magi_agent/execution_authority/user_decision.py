"""Dormant reference authentication for first-party user-decision receipts.

This module deliberately performs authentication only.  It neither persists a
receipt nor attaches the verifier to a live runtime route.  Storage owns nonce
uniqueness and idempotent replay; this boundary turns an ephemeral, signed
envelope into the immutable receipt that storage is allowed to observe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
import hmac
import json
from typing import Protocol, runtime_checkable

from magi_agent.execution_authority.contracts import (
    UserDecisionReceipt,
    UserDecisionRequest,
    validate_user_decision_receipt_binding,
)
from magi_agent.execution_authority.ports import AuthoritativeClockPort
from magi_agent.ops.safety import require_digest


HMAC_SHA256_DOMAIN_SEPARATOR = b"magi.user_decision.hmac-sha256.v1\x00"

_HMAC_SHA256_SIZE = 32
_MINIMUM_HMAC_KEY_SIZE = 32
_MINIMUM_NONCE_SIZE = 16
_MAXIMUM_NONCE_SIZE = 64
_MAXIMUM_RECEIPT_PAYLOAD_SIZE = 1_048_576


class UserDecisionAuthenticationError(ValueError):
    """The decision envelope was not authenticated against the exact request."""


def _require_nonempty_exact_string(value: object, *, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a nonempty exact string")
    return value


@dataclass(frozen=True, slots=True)
class HmacUserDecisionEnvelope:
    """Ephemeral authentication input whose raw fields must never be persisted."""

    key_id: str
    channel: str
    authentication_context_digest: str
    nonce: bytes = field(repr=False)
    canonical_receipt_payload: bytes = field(repr=False)
    signature: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonempty_exact_string(self.key_id, field_name="key_id")
        _require_nonempty_exact_string(self.channel, field_name="channel")
        if type(self.authentication_context_digest) is not str:
            raise ValueError("authentication_context_digest must be an exact string")
        require_digest(self.authentication_context_digest)
        if type(self.nonce) is not bytes or not (
            _MINIMUM_NONCE_SIZE <= len(self.nonce) <= _MAXIMUM_NONCE_SIZE
        ):
            raise ValueError("nonce must be an exact bounded byte sequence")
        if type(self.canonical_receipt_payload) is not bytes or not (
            0 < len(self.canonical_receipt_payload) <= _MAXIMUM_RECEIPT_PAYLOAD_SIZE
        ):
            raise ValueError("canonical_receipt_payload must be an exact bounded byte sequence")
        if type(self.signature) is not bytes or len(self.signature) != _HMAC_SHA256_SIZE:
            raise ValueError("signature must be an exact HMAC-SHA-256 byte sequence")


@dataclass(frozen=True, slots=True)
class HmacUserDecisionKey:
    """Opaque in-process HMAC key handle with a redacted representation."""

    key_id: str
    _secret: bytes = field(repr=False)

    def __init__(self, key_id: str, secret: bytes) -> None:
        _require_nonempty_exact_string(key_id, field_name="key_id")
        if type(secret) is not bytes or len(secret) < _MINIMUM_HMAC_KEY_SIZE:
            raise ValueError(
                "HMAC key material must be an exact byte sequence of at least 32 bytes"
            )
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "_secret", bytes(secret))

    def matches(self, *, message: bytes, signature: bytes) -> bool:
        """Authenticate without exposing the key or computed MAC to the caller."""

        if type(message) is not bytes or type(signature) is not bytes:
            return False
        expected = hmac.digest(self._secret, message, "sha256")
        return hmac.compare_digest(expected, signature)


@runtime_checkable
class HmacUserDecisionKeyPort(Protocol):
    """Resolve a channel-bound key handle from trusted request identity."""

    def key_for(
        self,
        *,
        key_id: str,
        tenant_id: str,
        principal_id: str,
        channel: str,
        authentication_context_digest: str,
    ) -> HmacUserDecisionKey | None: ...


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _signed_bytes(envelope: HmacUserDecisionEnvelope) -> bytes:
    """Domain-separated, unambiguous bytes covered by the reference MAC."""

    return b"".join(
        (
            HMAC_SHA256_DOMAIN_SEPARATOR,
            _length_prefix(envelope.key_id.encode("utf-8")),
            _length_prefix(envelope.channel.encode("utf-8")),
            _length_prefix(envelope.authentication_context_digest.encode("ascii")),
            _length_prefix(envelope.nonce),
            _length_prefix(envelope.canonical_receipt_payload),
        )
    )


def _canonical_receipt_payload(receipt: UserDecisionReceipt) -> bytes:
    return json.dumps(
        receipt.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


class HmacUserDecisionVerifier:
    """Standard-library HMAC-SHA-256 conformance verifier."""

    def __init__(
        self,
        *,
        keys: HmacUserDecisionKeyPort,
        clock: AuthoritativeClockPort,
    ) -> None:
        self._keys = keys
        self._clock = clock

    def verify(
        self,
        *,
        opaque_envelope: object,
        request: UserDecisionRequest,
    ) -> UserDecisionReceipt:
        try:
            return self._verify(opaque_envelope=opaque_envelope, request=request)
        except UserDecisionAuthenticationError:
            raise
        except Exception:
            # Authentication failures are intentionally uniform and never
            # interpolate envelope, nonce, signature, payload, or key material.
            raise UserDecisionAuthenticationError("user decision authentication failed") from None

    def _verify(
        self,
        *,
        opaque_envelope: object,
        request: UserDecisionRequest,
    ) -> UserDecisionReceipt:
        if type(opaque_envelope) is not HmacUserDecisionEnvelope:
            raise UserDecisionAuthenticationError("user decision authentication failed")
        if type(request) is not UserDecisionRequest:
            raise UserDecisionAuthenticationError("user decision authentication failed")
        envelope = opaque_envelope

        key = self._keys.key_for(
            key_id=envelope.key_id,
            tenant_id=request.tenant_id,
            principal_id=request.principal_id,
            channel=envelope.channel,
            authentication_context_digest=envelope.authentication_context_digest,
        )
        if type(key) is not HmacUserDecisionKey or key.key_id != envelope.key_id:
            raise UserDecisionAuthenticationError("user decision authentication failed")
        if not key.matches(message=_signed_bytes(envelope), signature=envelope.signature):
            raise UserDecisionAuthenticationError("user decision authentication failed")

        receipt = UserDecisionReceipt.model_validate_json(envelope.canonical_receipt_payload)
        canonical_payload = _canonical_receipt_payload(receipt)
        if not hmac.compare_digest(canonical_payload, envelope.canonical_receipt_payload):
            raise UserDecisionAuthenticationError("user decision authentication failed")
        if receipt.authentication_key_id != envelope.key_id:
            raise UserDecisionAuthenticationError("user decision authentication failed")
        if receipt.authentication_context_digest != envelope.authentication_context_digest:
            raise UserDecisionAuthenticationError("user decision authentication failed")
        expected_nonce_digest = "sha256:" + sha256(envelope.nonce).hexdigest()
        if not hmac.compare_digest(
            receipt.authentication_nonce_digest,
            expected_nonce_digest,
        ):
            raise UserDecisionAuthenticationError("user decision authentication failed")

        validate_user_decision_receipt_binding(request, receipt)
        now = self._clock.now()
        if (
            type(now) is not type(receipt.issued_at)
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
            or now < request.created_at
            or now < receipt.issued_at
            or now >= request.expires_at
            or now >= receipt.expires_at
        ):
            raise UserDecisionAuthenticationError("user decision authentication failed")
        return receipt


__all__ = [
    "HMAC_SHA256_DOMAIN_SEPARATOR",
    "HmacUserDecisionEnvelope",
    "HmacUserDecisionKey",
    "HmacUserDecisionKeyPort",
    "HmacUserDecisionVerifier",
    "UserDecisionAuthenticationError",
]
