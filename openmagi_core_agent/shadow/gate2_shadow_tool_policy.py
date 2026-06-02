from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


Gate2MutationStatus = Literal["simulated", "duplicate", "conflict", "denied", "rolled_back"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_SAFE_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
GATE2_ALLOWED_SANDBOX_ACTIONS = ("FileCreate", "FileEdit", "PatchApply")
GATE2_FORBIDDEN_ACTIONS = (
    "Bash",
    "TestRun",
    "FileWrite",
    "FileEditProduction",
    "PatchApplyProduction",
    "Delete",
    "FileDelete",
    "MemoryWrite",
    "BrowserAction",
    "WebFetch",
    "TelegramSend",
    "DiscordSend",
    "FileDeliver",
    "FileSend",
    "CronCreate",
    "CronUpdate",
    "CronDelete",
    "TaskCreate",
    "TaskStop",
    "ConnectorCredentialUse",
    "NetworkEgress",
    "WorkspaceMutationProduction",
)
_SENSITIVE_PATH_RE = re.compile(
    r"(^|/)(?:\.|.*(?:auth|cookie|credential|env|key|kube|password|secret|"
    r"session|token).*)($|/)",
    re.IGNORECASE,
)
_GATE2_LOOP_A_PREFIX = "gate2-loop-a/"
_GATE2_LOOP_A_SYNTHETIC_PATH_RE = re.compile(
    r"^gate2-loop-a/src/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.(?:txt|py)$"
)
_GATE2_LOOP_A_FORBIDDEN_PATH_RE = re.compile(
    r"(^|/)(?:\.|.*(?:auth|cookie|credential|key|kube|password|secret|"
    r"session|token).*)($|/)",
    re.IGNORECASE,
)
_ALLOWED_PUBLIC_METADATA_KEYS = frozenset(
    {"pathDigest", "contentDigest", "patchDigest"}
)


class _Gate2PolicyModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="json")
        if update:
            data.update(update)
        return type(self).model_validate(data)


class Gate2MutationReceipt(_Gate2PolicyModel):
    schema_version: Literal["gate2.shadowMutationReceipt.v1"] = Field(
        default="gate2.shadowMutationReceipt.v1",
        alias="schemaVersion",
    )
    request_digest: str = Field(alias="requestDigest")
    attempt_digest: str = Field(alias="attemptDigest")
    idempotency_key_digest: str = Field(alias="idempotencyKeyDigest")
    action: str
    status: Gate2MutationStatus
    path_digest: str = Field(alias="pathDigest")
    content_digest: str | None = Field(default=None, alias="contentDigest")
    patch_digest: str | None = Field(default=None, alias="patchDigest")
    receipt_digest: str = Field(alias="receiptDigest")
    denied_reason: str | None = Field(default=None, alias="deniedReason")
    public_metadata: Mapping[str, str] = Field(
        default_factory=dict,
        alias="publicMetadata",
    )

    @model_validator(mode="after")
    def _validate_public_receipt(self) -> Self:
        for digest in (
            self.request_digest,
            self.attempt_digest,
            self.idempotency_key_digest,
            self.path_digest,
            self.receipt_digest,
            self.content_digest,
            self.patch_digest,
        ):
            if digest is not None and not _DIGEST_RE.fullmatch(digest):
                raise ValueError("Gate 2 receipts must be digest-only")
        if not _SAFE_ACTION_RE.fullmatch(self.action):
            raise ValueError("Gate 2 action must be public-safe")
        for value in self.public_metadata.values():
            if not _DIGEST_RE.fullmatch(value):
                raise ValueError("Gate 2 public metadata must be digest-only")
        if not set(self.public_metadata).issubset(_ALLOWED_PUBLIC_METADATA_KEYS):
            raise ValueError("Gate 2 public metadata keys must be public-safe")
        if self.denied_reason is not None and not re.fullmatch(
            r"[a-z][a-z0-9_]{0,95}",
            self.denied_reason,
        ):
            raise ValueError("Gate 2 denied reason must be public-safe")
        object.__setattr__(
            self,
            "public_metadata",
            MappingProxyType(dict(self.public_metadata)),
        )
        return self

    @field_serializer("public_metadata")
    def _serialize_public_metadata(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class Gate2RollbackReceipt(_Gate2PolicyModel):
    schema_version: Literal["gate2.shadowRollbackReceipt.v1"] = Field(
        default="gate2.shadowRollbackReceipt.v1",
        alias="schemaVersion",
    )
    request_digest: str = Field(alias="requestDigest")
    mutation_receipt_digest: str = Field(alias="mutationReceiptDigest")
    rollback_digest: str = Field(alias="rollbackDigest")
    rollback_action: Literal["delete", "restore"] = Field(alias="rollbackAction")
    post_rollback_digest: str = Field(alias="postRollbackDigest")
    rollback_verified: Literal[True] = Field(default=True, alias="rollbackVerified")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @model_validator(mode="after")
    def _validate_public_rollback(self) -> Self:
        for digest in (
            self.request_digest,
            self.mutation_receipt_digest,
            self.rollback_digest,
            self.post_rollback_digest,
        ):
            if not _DIGEST_RE.fullmatch(digest):
                raise ValueError("Gate 2 rollback receipts must be digest-only")
        return self


class Gate2MutationOutcome(_Gate2PolicyModel):
    status: Gate2MutationStatus
    reason: str
    receipt: Gate2MutationReceipt
    rollback_receipt: Gate2RollbackReceipt | None = Field(
        default=None,
        alias="rollbackReceipt",
    )
    handler_called: bool = Field(default=False, alias="handlerCalled")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @model_validator(mode="after")
    def _validate_public_outcome(self) -> Self:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", self.reason):
            raise ValueError("Gate 2 reason must be public-safe")
        return self


class Gate2ShadowWorkspaceToolPolicy:
    def __init__(
        self,
        *,
        allowed_actions: tuple[str, ...] = GATE2_ALLOWED_SANDBOX_ACTIONS,
        forbidden_actions: tuple[str, ...] = GATE2_FORBIDDEN_ACTIONS,
    ) -> None:
        self.allowed_actions = tuple(
            action
            for action in allowed_actions
            if action in GATE2_ALLOWED_SANDBOX_ACTIONS
            and action not in GATE2_FORBIDDEN_ACTIONS
        )
        self.forbidden_actions = tuple(dict.fromkeys((*forbidden_actions, *GATE2_FORBIDDEN_ACTIONS)))
        self._receipts_by_idempotency: dict[str, Gate2MutationReceipt] = {}
        self._fingerprints_by_idempotency: dict[str, str] = {}

    @classmethod
    def default(cls) -> "Gate2ShadowWorkspaceToolPolicy":
        return cls()

    def evaluate_action(
        self,
        *,
        action: str,
        requestDigest: str,
        idempotencyKey: str,
        relativePath: str | None = None,
        content: str | None = None,
        patchDigest: str | None = None,
        command: str | None = None,
    ) -> Gate2MutationOutcome:
        del command
        action_name = _safe_action_label(action)
        request_digest = _safe_digest_or_digest(requestDigest)
        idempotency_digest = _idempotency_digest(
            idempotencyKey,
            request_digest=request_digest,
        )
        path_digest, path_denied_reason = _safe_path_digest_and_denial(relativePath)
        content_digest = _digest(content) if content is not None else None
        patch_digest = _safe_digest_or_digest(patchDigest) if patchDigest else None
        fingerprint = _digest(
            {
                "action": action_name,
                "pathDigest": path_digest,
                "contentDigest": content_digest,
                "patchDigest": patch_digest,
            }
        )
        attempt_digest = _digest(
            {
                "requestDigest": request_digest,
                "idempotencyDigest": idempotency_digest,
                "fingerprint": fingerprint,
            }
        )
        denied_reason = _action_denied_reason(
            action_name=action_name,
            allowed_actions=self.allowed_actions,
            forbidden_actions=self.forbidden_actions,
        )
        if denied_reason is None:
            denied_reason = path_denied_reason
        if denied_reason is not None:
            receipt = _receipt(
                request_digest=request_digest,
                attempt_digest=attempt_digest,
                idempotency_digest=idempotency_digest,
                action=action_name,
                status="denied",
                path_digest=path_digest,
                content_digest=content_digest,
                patch_digest=patch_digest,
                denied_reason=denied_reason,
            )
            return Gate2MutationOutcome(
                status="denied",
                reason=denied_reason,
                receipt=receipt,
                handlerCalled=False,
            )
        existing = self._receipts_by_idempotency.get(idempotency_digest)
        if existing is not None:
            if self._fingerprints_by_idempotency[idempotency_digest] == fingerprint:
                return Gate2MutationOutcome(
                    status="duplicate",
                    reason="duplicate_idempotency_key",
                    receipt=existing,
                    handlerCalled=False,
                )
            receipt = _receipt(
                request_digest=request_digest,
                attempt_digest=attempt_digest,
                idempotency_digest=idempotency_digest,
                action=action_name,
                status="conflict",
                path_digest=path_digest,
                content_digest=content_digest,
                patch_digest=patch_digest,
                denied_reason="idempotency_conflict",
            )
            return Gate2MutationOutcome(
                status="conflict",
                reason="idempotency_conflict",
                receipt=receipt,
                handlerCalled=False,
            )
        receipt = _receipt(
            request_digest=request_digest,
            attempt_digest=attempt_digest,
            idempotency_digest=idempotency_digest,
            action=action_name,
            status="simulated",
            path_digest=path_digest,
            content_digest=content_digest,
            patch_digest=patch_digest,
            denied_reason=None,
        )
        self._receipts_by_idempotency[idempotency_digest] = receipt
        self._fingerprints_by_idempotency[idempotency_digest] = fingerprint
        return Gate2MutationOutcome(
            status="simulated",
            reason="sandbox_mutation_simulated",
            receipt=receipt,
            handlerCalled=True,
        )


class Gate2SandboxMutationProvider:
    def __init__(self, *, policy: Gate2ShadowWorkspaceToolPolicy | None = None) -> None:
        self.policy = policy or Gate2ShadowWorkspaceToolPolicy.default()
        self._mutation_receipts: dict[str, Gate2MutationReceipt] = {}
        self._rollback_receipts: dict[str, Gate2RollbackReceipt] = {}

    def simulate_mutation(self, **kwargs: object) -> Gate2MutationOutcome:
        outcome = self.policy.evaluate_action(**kwargs)
        if outcome.status == "simulated":
            self._mutation_receipts[outcome.receipt.receipt_digest] = outcome.receipt
        return outcome

    def rollback(
        self,
        *,
        mutationReceiptDigest: str,
        requestDigest: str,
        rollbackAction: Literal["delete", "restore"],
        postRollbackDigest: str,
    ) -> Gate2MutationOutcome:
        mutation_digest = _safe_digest_or_digest(mutationReceiptDigest)
        request_digest = _safe_digest_or_digest(requestDigest)
        post_rollback_digest = _safe_digest_or_digest(postRollbackDigest)
        mutation_receipt = self._mutation_receipts.get(mutation_digest)
        existing_rollback = self._rollback_receipts.get(mutation_digest)
        if mutation_receipt is not None and mutation_receipt.request_digest != request_digest:
            receipt = _receipt(
                request_digest=request_digest,
                attempt_digest=_digest({"rollback": mutation_digest}),
                idempotency_digest=_digest(
                    {
                        "rollback": mutation_digest,
                        "requestDigest": request_digest,
                        "requestMismatch": True,
                    }
                ),
                action="Rollback",
                status="denied",
                path_digest=_digest("missing"),
                content_digest=None,
                patch_digest=None,
                denied_reason="rollback_request_mismatch",
            )
            return Gate2MutationOutcome(
                status="denied",
                reason="rollback_request_mismatch",
                receipt=receipt,
                handlerCalled=False,
            )
        if existing_rollback is not None and mutation_receipt is not None:
            return Gate2MutationOutcome(
                status="duplicate",
                reason="duplicate_rollback",
                receipt=mutation_receipt,
                rollbackReceipt=existing_rollback,
                handlerCalled=False,
            )
        if mutation_receipt is None:
            receipt = _receipt(
                request_digest=request_digest,
                attempt_digest=_digest({"rollback": mutation_digest}),
                idempotency_digest=_digest({"rollback": mutation_digest, "missing": True}),
                action="Rollback",
                status="denied",
                path_digest=_digest("missing"),
                content_digest=None,
                patch_digest=None,
                denied_reason="mutation_receipt_not_found",
            )
            return Gate2MutationOutcome(
                status="denied",
                reason="mutation_receipt_not_found",
                receipt=receipt,
                handlerCalled=False,
            )
        rollback_receipt = Gate2RollbackReceipt(
            requestDigest=request_digest,
            mutationReceiptDigest=mutation_receipt.receipt_digest,
            rollbackDigest=_digest(
                {
                    "requestDigest": request_digest,
                    "mutationReceiptDigest": mutation_receipt.receipt_digest,
                    "rollbackAction": rollbackAction,
                    "postRollbackDigest": post_rollback_digest,
                    "rollbackVerified": True,
                }
            ),
            rollbackAction=rollbackAction,
            postRollbackDigest=post_rollback_digest,
            rollbackVerified=True,
        )
        self._rollback_receipts[mutation_digest] = rollback_receipt
        return Gate2MutationOutcome(
            status="rolled_back",
            reason="sandbox_rollback_simulated",
            receipt=mutation_receipt,
            rollbackReceipt=rollback_receipt,
            handlerCalled=True,
        )


def _receipt(
    *,
    request_digest: str,
    attempt_digest: str,
    idempotency_digest: str,
    action: str,
    status: Gate2MutationStatus,
    path_digest: str,
    content_digest: str | None,
    patch_digest: str | None,
    denied_reason: str | None,
) -> Gate2MutationReceipt:
    public_metadata = {"pathDigest": path_digest}
    if content_digest is not None:
        public_metadata["contentDigest"] = content_digest
    if patch_digest is not None:
        public_metadata["patchDigest"] = patch_digest
    receipt_digest = _digest(
        {
            "schema": "gate2.shadowMutationReceipt.v1",
            "requestDigest": request_digest,
            "attemptDigest": attempt_digest,
            "idempotencyDigest": idempotency_digest,
            "action": action,
            "status": status,
            "pathDigest": path_digest,
            "contentDigest": content_digest,
            "patchDigest": patch_digest,
            "deniedReason": denied_reason,
        }
    )
    return Gate2MutationReceipt(
        requestDigest=request_digest,
        attemptDigest=attempt_digest,
        idempotencyKeyDigest=idempotency_digest,
        action=action,
        status=status,
        pathDigest=path_digest,
        contentDigest=content_digest,
        patchDigest=patch_digest,
        receiptDigest=receipt_digest,
        deniedReason=denied_reason,
        publicMetadata=public_metadata,
    )


def _safe_action_label(value: object) -> str:
    action = str(value or "").strip()
    if not _SAFE_ACTION_RE.fullmatch(action):
        return "InvalidAction"
    return action


def _action_denied_reason(
    *,
    action_name: str,
    allowed_actions: tuple[str, ...],
    forbidden_actions: tuple[str, ...],
) -> str | None:
    if action_name == "InvalidAction":
        return "malformed_gate2_action"
    if action_name not in allowed_actions or action_name in forbidden_actions:
        return "forbidden_gate2_action"
    return None


def _safe_path_digest_and_denial(value: str | None) -> tuple[str, str | None]:
    path = str(value or "").replace("\\", "/").strip()
    if not path or path.startswith(("/", "~")) or ".." in path.split("/"):
        return _digest({"path": "denied"}), "path_policy_denied"
    if path.startswith(_GATE2_LOOP_A_PREFIX):
        if _safe_gate2_loop_a_synthetic_path(path):
            return _digest({"path": path}), None
        return _digest({"path": "protected"}), "path_policy_denied"
    if _SENSITIVE_PATH_RE.search(path):
        return _digest({"path": "protected"}), "path_policy_denied"
    return _digest({"path": path}), None


def _safe_gate2_loop_a_synthetic_path(path: str) -> bool:
    if not _GATE2_LOOP_A_SYNTHETIC_PATH_RE.fullmatch(path):
        return False
    parts = [part for part in path.split("/") if part]
    if any(part.startswith(".") for part in parts):
        return False
    return _GATE2_LOOP_A_FORBIDDEN_PATH_RE.search(path) is None


def _idempotency_digest(value: str, *, request_digest: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_IDEMPOTENCY_RE.fullmatch(text):
        text = _digest(text)
    return _digest({"idempotencyKey": text, "requestDigest": request_digest})


def _safe_digest_or_digest(value: object) -> str:
    if isinstance(value, str) and _DIGEST_RE.fullmatch(value):
        return value
    return _digest(value)


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
