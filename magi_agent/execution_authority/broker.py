"""Dormant universal effect-admission broker.

The module is intentionally not attached to any runtime route.  It defines the
fail-closed orchestration seam used by later storage and executor adapters.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterable, Mapping
from hashlib import sha256
import hmac
import json
import secrets
from threading import Lock
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.execution_authority.envelopes import EffectDeclarationBinding
from magi_agent.ops.safety import require_digest


class BrokerError(RuntimeError):
    """Base class for fail-closed broker failures."""


class DuplicateEffectRegistration(BrokerError):
    """Raised when an exact effect name is registered more than once."""


class UndeclaredEffect(BrokerError):
    """Raised when a request names no reviewed effect declaration."""


class InvalidExecutionToken(BrokerError):
    """Raised before handoff when an execution token is not exact and live."""


class EffectRegistry:
    """Startup-time registry of immutable, reviewed effect declarations."""

    def __init__(
        self,
        declarations: Iterable[EffectDeclarationBinding] = (),
    ) -> None:
        self._declarations: dict[str, EffectDeclarationBinding] = {}
        for declaration in declarations:
            self.register(declaration)

    def register(self, declaration: EffectDeclarationBinding) -> None:
        if type(declaration) is not EffectDeclarationBinding:
            raise TypeError("effect declarations must be exact EffectDeclarationBinding values")
        validated = EffectDeclarationBinding.model_validate(
            declaration.model_dump(by_alias=True, mode="python")
        )
        if validated.effect_name in self._declarations:
            raise DuplicateEffectRegistration(validated.effect_name)
        self._declarations[validated.effect_name] = validated

    def require(self, effect_name: str) -> EffectDeclarationBinding:
        if type(effect_name) is not str or not effect_name:
            raise UndeclaredEffect("effect name must be a nonempty exact string")
        try:
            return self._declarations[effect_name]
        except KeyError as exc:
            raise UndeclaredEffect(effect_name) from exc

    def snapshot(self) -> Mapping[str, EffectDeclarationBinding]:
        return MappingProxyType(dict(self._declarations))


class ExecutionTokenClaims(BaseModel):
    """Authenticated, exact-request claims carried only to executor handoff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1, le=1)
    action_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    request_digest: str
    authority_digest: str
    fencing_token: int = Field(ge=0)
    expires_unix_ms: int = Field(ge=0)
    executor_digest: str
    precondition_digest: str
    nonce: str = Field(min_length=1)

    @field_validator(
        "request_digest",
        "authority_digest",
        "executor_digest",
        "precondition_digest",
    )
    @classmethod
    def _require_digest(cls, value: str) -> str:
        return require_digest(value)


def _b64encode(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if type(value) is not str or not value:
        raise InvalidExecutionToken("execution token segment is invalid")
    try:
        return urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise InvalidExecutionToken("execution token encoding is invalid") from exc


class ExecutionTokenIssuer:
    """HMAC issuer with an atomic in-process single-use handoff ledger.

    Durable single-use authority consumption belongs to the injected journal
    adapter.  This ledger independently prevents a token from being handed to
    an executor twice within one broker process.
    """

    def __init__(
        self,
        *,
        key: bytes,
        nonce_factory: Callable[[], bytes] = lambda: secrets.token_bytes(16),
    ) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("execution token key must be at least 32 exact bytes")
        if not callable(nonce_factory):
            raise TypeError("nonce_factory must be callable")
        self._key = key
        self._nonce_factory = nonce_factory
        self._consumed_nonces: set[str] = set()
        self._consume_lock = Lock()

    def issue(
        self,
        *,
        action_id: str,
        attempt_id: str,
        request_digest: str,
        authority_digest: str,
        fencing_token: int,
        expires_unix_ms: int,
        executor_digest: str,
        precondition_digest: str,
    ) -> str:
        nonce_bytes = self._nonce_factory()
        if type(nonce_bytes) is not bytes or len(nonce_bytes) < 16:
            raise ValueError("nonce_factory must return at least 16 exact bytes")
        claims = ExecutionTokenClaims(
            version=1,
            action_id=action_id,
            attempt_id=attempt_id,
            request_digest=request_digest,
            authority_digest=authority_digest,
            fencing_token=fencing_token,
            expires_unix_ms=expires_unix_ms,
            executor_digest=executor_digest,
            precondition_digest=precondition_digest,
            nonce=_b64encode(nonce_bytes),
        )
        payload = json.dumps(
            claims.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        signature = hmac.digest(self._key, payload, "sha256")
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def verify(
        self,
        token: str,
        *,
        action_id: str,
        attempt_id: str,
        request_digest: str,
        authority_digest: str,
        fencing_token: int,
        now_unix_ms: int,
        executor_digest: str,
        precondition_digest: str,
    ) -> ExecutionTokenClaims:
        claims = self._decode_and_authenticate(token)
        expected = (
            ("actionId", claims.action_id, action_id),
            ("attemptId", claims.attempt_id, attempt_id),
            ("requestDigest", claims.request_digest, request_digest),
            ("authorityDigest", claims.authority_digest, authority_digest),
            ("fencingToken", claims.fencing_token, fencing_token),
            ("executorDigest", claims.executor_digest, executor_digest),
            (
                "preconditionDigest",
                claims.precondition_digest,
                precondition_digest,
            ),
        )
        if any(observed != wanted for _, observed, wanted in expected):
            raise InvalidExecutionToken("execution token binding mismatch")
        if type(now_unix_ms) is not int or type(now_unix_ms) is bool:
            raise InvalidExecutionToken("execution token clock is invalid")
        if now_unix_ms > claims.expires_unix_ms:
            raise InvalidExecutionToken("execution token expired")
        return claims

    def consume(self, token: str, **expected: Any) -> ExecutionTokenClaims:
        claims = self.verify(token, **expected)
        with self._consume_lock:
            if claims.nonce in self._consumed_nonces:
                raise InvalidExecutionToken("execution token already consumed")
            self._consumed_nonces.add(claims.nonce)
        return claims

    def token_digest(self, token: str) -> str:
        if type(token) is not str:
            raise TypeError("execution token must be an exact string")
        return "sha256:" + sha256(token.encode("ascii")).hexdigest()

    def _decode_and_authenticate(self, token: str) -> ExecutionTokenClaims:
        if type(token) is not str:
            raise InvalidExecutionToken("execution token must be an exact string")
        try:
            encoded_payload, encoded_signature = token.split(".")
        except ValueError as exc:
            raise InvalidExecutionToken("execution token shape is invalid") from exc
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
        expected_signature = hmac.digest(self._key, payload, "sha256")
        if not hmac.compare_digest(signature, expected_signature):
            raise InvalidExecutionToken("execution token signature is invalid")
        try:
            raw = json.loads(payload, parse_constant=lambda value: None)
            claims = ExecutionTokenClaims.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise InvalidExecutionToken("execution token claims are invalid") from exc
        return claims


__all__ = [
    "BrokerError",
    "DuplicateEffectRegistration",
    "EffectRegistry",
    "ExecutionTokenClaims",
    "ExecutionTokenIssuer",
    "InvalidExecutionToken",
    "UndeclaredEffect",
]
