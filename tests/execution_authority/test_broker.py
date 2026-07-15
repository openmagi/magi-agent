from __future__ import annotations

import pytest

from magi_agent.execution_authority.broker import (
    DuplicateEffectRegistration,
    EffectRegistry,
    ExecutionTokenIssuer,
    InvalidExecutionToken,
    UndeclaredEffect,
)
from magi_agent.execution_authority.envelopes import (
    EffectDeclarationBinding,
    canonical_provider_guarantees_digest,
)
from magi_agent.execution_authority.state_machine import (
    EffectClass,
    IdempotencyCapability,
    ProviderGuarantee,
    RecoveryStrategy,
    ResourceSemantics,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _declaration(name: str = "FileWrite") -> EffectDeclarationBinding:
    guarantees = (ProviderGuarantee.LOCAL_ATOMIC,)
    return EffectDeclarationBinding(
        effectName=name,
        effectClass=EffectClass.WORKSPACE_WRITE,
        resourceSemantics=ResourceSemantics.WORKSPACE_TRANSACTION,
        handlerDigest=_digest("1"),
        normalizerDigest=_digest("2"),
        resourceDeriverDigest=_digest("3"),
        executorDigest=_digest("4"),
        recoveryAdapterDigest=_digest("5"),
        providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
        providerGuarantees=guarantees,
        idempotencyCapability=IdempotencyCapability.LOCAL_GENERATION_CAS,
        recoveryStrategy=RecoveryStrategy.WORKSPACE_TRANSACTION,
    )


def test_effect_registry_rejects_unknown_and_duplicate_effects() -> None:
    registry = EffectRegistry()

    with pytest.raises(UndeclaredEffect):
        registry.require("MissingTool")

    declaration = _declaration()
    registry.register(declaration)

    assert registry.require("FileWrite") == declaration
    with pytest.raises(DuplicateEffectRegistration):
        registry.register(declaration)


def test_effect_registry_snapshot_is_read_only() -> None:
    registry = EffectRegistry((_declaration(),))

    snapshot = registry.snapshot()

    with pytest.raises(TypeError):
        snapshot["Other"] = _declaration("Other")  # type: ignore[index]


def test_execution_token_binds_exact_request_and_is_single_use() -> None:
    issuer = ExecutionTokenIssuer(key=b"k" * 32, nonce_factory=lambda: b"n" * 16)
    token = issuer.issue(
        action_id="action_1",
        attempt_id="attempt_1",
        request_digest=_digest("5"),
        authority_digest=_digest("6"),
        fencing_token=8,
        expires_unix_ms=2_000,
        executor_digest=_digest("7"),
        precondition_digest=_digest("8"),
    )

    claims = issuer.consume(
        token,
        action_id="action_1",
        attempt_id="attempt_1",
        request_digest=_digest("5"),
        authority_digest=_digest("6"),
        fencing_token=8,
        now_unix_ms=1_000,
        executor_digest=_digest("7"),
        precondition_digest=_digest("8"),
    )

    assert claims.action_id == "action_1"
    with pytest.raises(InvalidExecutionToken, match="already consumed"):
        issuer.consume(
            token,
            action_id="action_1",
            attempt_id="attempt_1",
            request_digest=_digest("5"),
            authority_digest=_digest("6"),
            fencing_token=8,
            now_unix_ms=1_000,
            executor_digest=_digest("7"),
            precondition_digest=_digest("8"),
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"request_digest": _digest("9")}, "binding"),
        ({"fencing_token": 9}, "binding"),
        ({"executor_digest": _digest("9")}, "binding"),
        ({"precondition_digest": _digest("9")}, "binding"),
        ({"now_unix_ms": 2_001}, "expired"),
    ],
)
def test_execution_token_rejects_mismatch_and_expiry(
    override: dict[str, object],
    message: str,
) -> None:
    issuer = ExecutionTokenIssuer(key=b"k" * 32, nonce_factory=lambda: b"n" * 16)
    token = issuer.issue(
        action_id="action_1",
        attempt_id="attempt_1",
        request_digest=_digest("5"),
        authority_digest=_digest("6"),
        fencing_token=8,
        expires_unix_ms=2_000,
        executor_digest=_digest("7"),
        precondition_digest=_digest("8"),
    )
    expected: dict[str, object] = {
        "action_id": "action_1",
        "attempt_id": "attempt_1",
        "request_digest": _digest("5"),
        "authority_digest": _digest("6"),
        "fencing_token": 8,
        "now_unix_ms": 1_000,
        "executor_digest": _digest("7"),
        "precondition_digest": _digest("8"),
    }
    expected.update(override)

    with pytest.raises(InvalidExecutionToken, match=message):
        issuer.verify(token, **expected)  # type: ignore[arg-type]


def test_execution_token_rejects_tampering() -> None:
    issuer = ExecutionTokenIssuer(key=b"k" * 32, nonce_factory=lambda: b"n" * 16)
    token = issuer.issue(
        action_id="action_1",
        attempt_id="attempt_1",
        request_digest=_digest("5"),
        authority_digest=_digest("6"),
        fencing_token=8,
        expires_unix_ms=2_000,
        executor_digest=_digest("7"),
        precondition_digest=_digest("8"),
    )
    payload, signature = token.split(".")
    tampered = f"{payload[:-1]}A.{signature}"

    with pytest.raises(InvalidExecutionToken):
        issuer.verify(
            tampered,
            action_id="action_1",
            attempt_id="attempt_1",
            request_digest=_digest("5"),
            authority_digest=_digest("6"),
            fencing_token=8,
            now_unix_ms=1_000,
            executor_digest=_digest("7"),
            precondition_digest=_digest("8"),
        )
