from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import json
from typing import Callable

import pytest

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    UserDecisionReceipt,
    UserDecisionRequest,
)
from magi_agent.execution_authority.user_decision import (
    HMAC_SHA256_DOMAIN_SEPARATOR,
    HmacUserDecisionEnvelope,
    HmacUserDecisionKey,
    HmacUserDecisionKeyPort,
    HmacUserDecisionVerifier,
    UserDecisionAuthenticationError,
)
from magi_agent.execution_authority.ports import UserDecisionVerifierPort


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
NONCE = b"n" * 32
SECRET = b"k" * 32
CHANNEL = "telegram"
AUTH_CONTEXT_DIGEST = "sha256:" + "5" * 64
WORKSPACE_REF = f"workspace://sha256:{'0' * 64}/report.md"


def _digest(character: str) -> str:
    return "sha256:" + character * 64


class FixedClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


class StaticKeyPort:
    def __init__(self, key: HmacUserDecisionKey | None) -> None:
        self.key = key
        self.calls: list[dict[str, object]] = []

    def key_for(
        self,
        *,
        key_id: str,
        tenant_id: str,
        principal_id: str,
        channel: str,
        authentication_context_digest: str,
    ) -> HmacUserDecisionKey | None:
        self.calls.append(
            {
                "key_id": key_id,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "channel": channel,
                "authentication_context_digest": authentication_context_digest,
            }
        )
        return self.key


def _request(**overrides: object) -> UserDecisionRequest:
    capability = AuthorityCapability(
        effectClass="workspace.write",
        resourceRef=WORKSPACE_REF,
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=_digest("8"),
    )
    payload: dict[str, object] = {
        "decisionRequestId": "decision_01",
        "principalId": "actor_01",
        "tenantId": "tenant_01",
        "sessionId": "session_01",
        "turnId": "turn_01",
        "taskContractId": "task_01",
        "taskVersion": 1,
        "taskContractDigest": _digest("1"),
        "completionEpochId": "epoch_01",
        "actionId": "action_01",
        "authorityPartitionId": "partition_01",
        "normalizedRequestDigest": _digest("2"),
        "capabilities": (capability,),
        "workspaceViewBindingDigest": _digest("8"),
        "authorityCeilingDigest": _digest("3"),
        "policyDigest": _digest("4"),
        "pendingEventId": "event_01",
        "reasonCodes": ("sensitive_write",),
        "createdAt": NOW - timedelta(minutes=1),
        "expiresAt": NOW + timedelta(minutes=4),
        "compareVersion": 1,
    }
    payload.update(overrides)
    return UserDecisionRequest.model_validate(payload)


def _receipt(
    request: UserDecisionRequest,
    *,
    nonce: bytes = NONCE,
    **overrides: object,
) -> UserDecisionReceipt:
    payload: dict[str, object] = {
        "receiptId": "receipt_01",
        "decisionRequestId": request.decision_request_id,
        "decision": "approve",
        "authenticatedActorId": request.principal_id,
        "authenticationKeyId": "key_01",
        "authenticationContextDigest": AUTH_CONTEXT_DIGEST,
        "authenticationNonceDigest": "sha256:" + sha256(nonce).hexdigest(),
        "transportReceiptDigest": _digest("7"),
        "principalId": request.principal_id,
        "tenantId": request.tenant_id,
        "sessionId": request.session_id,
        "turnId": request.turn_id,
        "taskContractId": request.task_contract_id,
        "taskVersion": request.task_version,
        "taskContractDigest": request.task_contract_digest,
        "completionEpochId": request.completion_epoch_id,
        "actionId": request.action_id,
        "authorityPartitionId": request.authority_partition_id,
        "normalizedRequestDigest": request.normalized_request_digest,
        "authorityCeilingDigest": request.authority_ceiling_digest,
        "policyDigest": request.policy_digest,
        "capabilitiesDigest": request.capabilities_digest,
        "workspaceViewBindingDigest": request.workspace_view_binding_digest,
        "issuedAt": NOW - timedelta(seconds=30),
        "expiresAt": NOW + timedelta(minutes=3),
    }
    payload.update(overrides)
    return UserDecisionReceipt.model_validate(payload)


def _canonical_payload(receipt: UserDecisionReceipt) -> bytes:
    return json.dumps(
        receipt.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _mac_input(
    *,
    key_id: str,
    channel: str,
    authentication_context_digest: str,
    nonce: bytes,
    payload: bytes,
) -> bytes:
    return b"".join(
        (
            HMAC_SHA256_DOMAIN_SEPARATOR,
            _length_prefix(key_id.encode("utf-8")),
            _length_prefix(channel.encode("utf-8")),
            _length_prefix(authentication_context_digest.encode("ascii")),
            _length_prefix(nonce),
            _length_prefix(payload),
        )
    )


def _envelope(
    receipt: UserDecisionReceipt,
    *,
    nonce: bytes = NONCE,
    secret: bytes = SECRET,
    key_id: str | None = None,
    channel: str = CHANNEL,
    authentication_context_digest: str = AUTH_CONTEXT_DIGEST,
    payload: bytes | None = None,
) -> HmacUserDecisionEnvelope:
    resolved_key_id = key_id or receipt.authentication_key_id
    resolved_payload = payload or _canonical_payload(receipt)
    signature = hmac.new(
        secret,
        _mac_input(
            key_id=resolved_key_id,
            channel=channel,
            authentication_context_digest=authentication_context_digest,
            nonce=nonce,
            payload=resolved_payload,
        ),
        "sha256",
    ).digest()
    return HmacUserDecisionEnvelope(
        key_id=resolved_key_id,
        channel=channel,
        authentication_context_digest=authentication_context_digest,
        nonce=nonce,
        canonical_receipt_payload=resolved_payload,
        signature=signature,
    )


def _verifier(
    *,
    clock: FixedClock | None = None,
    key_port: StaticKeyPort | None = None,
) -> tuple[HmacUserDecisionVerifier, StaticKeyPort]:
    resolved_port = key_port or StaticKeyPort(HmacUserDecisionKey("key_01", SECRET))
    return HmacUserDecisionVerifier(keys=resolved_port, clock=clock or FixedClock()), resolved_port


def test_golden_envelope_verifies_and_uses_bound_key_lookup() -> None:
    request = _request()
    receipt = _receipt(request)
    envelope = _envelope(receipt)
    verifier, key_port = _verifier()

    assert HMAC_SHA256_DOMAIN_SEPARATOR == b"magi.user_decision.hmac-sha256.v1\x00"
    assert envelope.signature.hex() == (
        "f27d8d307aa3055b9c3f66f956d48f077950fd74f75883b41e385871cae12027"
    )
    assert verifier.verify(opaque_envelope=envelope, request=request) == receipt
    assert key_port.calls == [
        {
            "key_id": "key_01",
            "tenant_id": "tenant_01",
            "principal_id": "actor_01",
            "channel": CHANNEL,
            "authentication_context_digest": AUTH_CONTEXT_DIGEST,
        }
    ]
    assert isinstance(key_port, HmacUserDecisionKeyPort)
    assert isinstance(verifier, UserDecisionVerifierPort)


@pytest.mark.parametrize(
    ("name", "mutate"),
    (
        (
            "payload",
            lambda value: replace(
                value,
                canonical_receipt_payload=value.canonical_receipt_payload.replace(
                    b"actor_01", b"actor_02", 1
                ),
            ),
        ),
        (
            "signature",
            lambda value: replace(
                value, signature=value.signature[:-1] + bytes((value.signature[-1] ^ 1,))
            ),
        ),
        (
            "nonce",
            lambda value: replace(value, nonce=value.nonce[:-1] + bytes((value.nonce[-1] ^ 1,))),
        ),
        ("channel", lambda value: replace(value, channel="telegran")),
        (
            "authentication-context",
            lambda value: replace(value, authentication_context_digest=_digest("6")),
        ),
        ("key-id", lambda value: replace(value, key_id="key_02")),
    ),
)
def test_single_byte_envelope_mutations_fail(
    name: str,
    mutate: Callable[[HmacUserDecisionEnvelope], HmacUserDecisionEnvelope],
) -> None:
    del name
    request = _request()
    envelope = _envelope(_receipt(request))
    verifier, _ = _verifier()

    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=mutate(envelope), request=request)


@pytest.mark.parametrize(
    "changes",
    (
        {"authenticatedActorId": "actor_02", "principalId": "actor_02"},
        {"tenantId": "tenant_02"},
        {"sessionId": "session_02"},
        {"turnId": "turn_02"},
        {"taskContractId": "task_02"},
        {"taskVersion": 2},
        {"taskContractDigest": _digest("a")},
        {"actionId": "action_02"},
    ),
)
def test_validly_signed_identity_or_task_drift_fails(changes: dict[str, object]) -> None:
    request = _request()
    drifted = _receipt(request, **changes)
    verifier, _ = _verifier()

    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=_envelope(drifted), request=request)


def test_nonce_digest_is_derived_from_signed_nonce() -> None:
    request = _request()
    receipt = _receipt(request, authenticationNonceDigest=_digest("9"))
    verifier, _ = _verifier()

    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=_envelope(receipt), request=request)


def test_envelope_metadata_must_match_signed_receipt() -> None:
    request = _request()
    receipt = _receipt(request)
    verifier, _ = _verifier()

    wrong_key = _envelope(receipt, key_id="key_02")
    wrong_context = _envelope(receipt, authentication_context_digest=_digest("6"))
    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=wrong_key, request=request)
    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=wrong_context, request=request)


def test_noncanonical_but_validly_signed_payload_fails() -> None:
    request = _request()
    receipt = _receipt(request)
    noncanonical = json.dumps(
        receipt.model_dump(by_alias=True, mode="json"),
        sort_keys=False,
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    verifier, _ = _verifier()

    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(
            opaque_envelope=_envelope(receipt, payload=noncanonical),
            request=request,
        )


@pytest.mark.parametrize(
    "clock_time",
    (
        NOW - timedelta(seconds=31),
        NOW + timedelta(minutes=3),
        NOW + timedelta(minutes=4),
    ),
)
def test_issue_and_expiry_window_is_checked_against_authoritative_clock(
    clock_time: datetime,
) -> None:
    request = _request()
    receipt = _receipt(request)
    verifier, _ = _verifier(clock=FixedClock(clock_time))

    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=_envelope(receipt), request=request)


def test_unknown_key_and_untyped_envelope_fail_closed() -> None:
    request = _request()
    receipt = _receipt(request)
    verifier, _ = _verifier(key_port=StaticKeyPort(None))

    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope=_envelope(receipt), request=request)
    with pytest.raises(UserDecisionAuthenticationError):
        verifier.verify(opaque_envelope={"payload": _canonical_payload(receipt)}, request=request)


def test_raw_authentication_material_is_redacted_and_never_returned() -> None:
    request = _request()
    receipt = _receipt(request)
    key = HmacUserDecisionKey("key_01", SECRET)
    envelope = _envelope(receipt)
    verifier, _ = _verifier(key_port=StaticKeyPort(key))

    verified = verifier.verify(opaque_envelope=envelope, request=request)

    assert verified == receipt
    assert not hasattr(verified, "nonce")
    assert not hasattr(verified, "signature")
    assert SECRET.hex() not in repr(key)
    assert NONCE.hex() not in repr(envelope)
    assert envelope.signature.hex() not in repr(envelope)


def test_authentication_failure_does_not_echo_secrets() -> None:
    request = _request()
    receipt = _receipt(request)
    envelope = replace(_envelope(receipt), signature=b"x" * 32)
    verifier, _ = _verifier()

    with pytest.raises(UserDecisionAuthenticationError) as raised:
        verifier.verify(opaque_envelope=envelope, request=request)

    rendered = f"{raised.value!r} {raised.value}"
    assert SECRET.hex() not in rendered
    assert NONCE.hex() not in rendered
    assert envelope.signature.hex() not in rendered
