"""Dormant transactional workspace writer.

The writer in this module is intentionally unattached to Magi Agent routes.  It
operates only through injected journal, lease, clock, token-verifier, and
filesystem boundaries.  The bundled local journal/lease implementations exist
for conformance tests and single-host development; a live attachment must use
the durable execution-authority store owned by the host.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Literal, Protocol, runtime_checkable

try:  # POSIX is the supported publication platform for this dormant slice.
    import fcntl
except ImportError:  # pragma: no cover - exercised by platform conformance.
    fcntl = None  # type: ignore[assignment]

from magi_agent.execution_authority.canonicalization import (
    CanonicalizationError,
    canonical_file_resource,
    require_canonical_workspace_resource_ref,
    workspace_relative_path,
)


_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_MAX_PLAN_ENTRIES = 1_024
_MAX_STAGED_BYTES = 128 * 1024 * 1024
_PROTECTED_TOP_LEVEL = frozenset(
    {
        ".git",
        ".magi-authority",
        "AGENTS.md",
        "CLAUDE.md",
        "HEARTBEAT.md",
        "SOUL.md",
        "TOOLS.md",
    }
)


class WorkspaceWriterError(RuntimeError):
    """Base class for fail-closed workspace writer errors."""


class WorkspaceConflict(WorkspaceWriterError):
    """A read, absence, generation, state-root, or fence precondition changed."""


class StaleWorkspaceFence(WorkspaceConflict):
    """The execution token no longer owns the workspace fence."""


class InvalidWorkspacePlan(WorkspaceWriterError):
    """A mutation plan is malformed or exceeds writer policy."""


class UnsupportedWorkspacePlatform(WorkspaceWriterError):
    """The host lacks publication primitives required by the writer."""


class MutationOperation(StrEnum):
    CREATE_FILE = "create_file"
    REPLACE_FILE = "replace_file"
    DELETE_FILE = "delete_file"
    CREATE_DIRECTORY = "create_directory"
    DELETE_DIRECTORY = "delete_directory"


class PublicationState(StrEnum):
    READY = "ready"
    STAGED = "staged"
    COMMIT_DECIDED = "commit_decided"
    COMMITTED = "committed"
    CONFLICT = "conflict"
    PARTIAL = "partial"
    QUARANTINED = "quarantined"
    NOT_EXECUTED = "not_executed"


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _digest_json(payload: object) -> str:
    return _digest_bytes(_canonical_json(payload))


def _require_digest(value: str, *, name: str) -> None:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a sha256 digest")


def _require_nonempty(value: str, *, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProofContext:
    task_contract_id: str
    task_version: int
    task_contract_digest: str
    completion_epoch_id: str
    evidence_id: str
    evidence_digest: str
    evidence_root: str
    producer_id: str
    producer_version: str
    producer_liveness: Literal["live"]

    def __post_init__(self) -> None:
        for name in (
            "task_contract_id",
            "completion_epoch_id",
            "evidence_id",
            "producer_id",
            "producer_version",
        ):
            _require_nonempty(getattr(self, name), name=name)
        if type(self.task_version) is not int or self.task_version < 1:
            raise ValueError("task_version must be a positive integer")
        for name in ("task_contract_digest", "evidence_digest", "evidence_root"):
            _require_digest(getattr(self, name), name=name)
        if self.producer_liveness != "live":
            raise ValueError("workspace proofs require a live producer")

    def to_wire(self) -> dict[str, object]:
        return {
            "completionEpochId": self.completion_epoch_id,
            "evidenceDigest": self.evidence_digest,
            "evidenceId": self.evidence_id,
            "evidenceRoot": self.evidence_root,
            "producerId": self.producer_id,
            "producerLiveness": self.producer_liveness,
            "producerVersion": self.producer_version,
            "taskContractDigest": self.task_contract_digest,
            "taskContractId": self.task_contract_id,
            "taskVersion": self.task_version,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceExecutionToken:
    token_id: str
    action_id: str
    attempt_id: str
    workspace_ref: str
    fencing_token: int
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("token_id", "action_id", "attempt_id"):
            _require_nonempty(getattr(self, name), name=name)
        require_canonical_workspace_resource_ref(self.workspace_ref)
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("fencing_token must be a positive integer")
        _require_aware(self.issued_at, name="issued_at")
        _require_aware(self.expires_at, name="expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("execution token must expire after it is issued")

    @property
    def token_digest(self) -> str:
        return _digest_json(
            {
                "actionId": self.action_id,
                "attemptId": self.attempt_id,
                "expiresAt": self.expires_at.isoformat(),
                "fencingToken": self.fencing_token,
                "issuedAt": self.issued_at.isoformat(),
                "tokenId": self.token_id,
                "workspaceRef": self.workspace_ref,
            }
        )


@runtime_checkable
class WorkspaceClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class WorkspaceExecutionTokenVerifierPort(Protocol):
    def verify(self, token: WorkspaceExecutionToken) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReadProof:
    proof_id: str
    resource_ref: str
    resource_kind: Literal["file", "directory"]
    content_digest: str | None
    identity_digest: str
    workspace_ref: str
    workspace_generation: int
    state_root: str
    context: ProofContext
    observed_at: datetime
    proof_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _require_nonempty(self.proof_id, name="proof_id")
        require_canonical_workspace_resource_ref(self.resource_ref)
        require_canonical_workspace_resource_ref(self.workspace_ref)
        if type(self.workspace_generation) is not int or self.workspace_generation < 0:
            raise ValueError("workspace_generation must be a non-negative integer")
        _require_digest(self.identity_digest, name="identity_digest")
        _require_digest(self.state_root, name="state_root")
        if self.resource_kind == "file":
            if self.content_digest is None:
                raise ValueError("file read proof requires content_digest")
            _require_digest(self.content_digest, name="content_digest")
        elif self.content_digest is not None:
            raise ValueError("directory read proof cannot carry content_digest")
        _require_aware(self.observed_at, name="observed_at")
        object.__setattr__(self, "proof_digest", _digest_json(self.to_wire()))

    def to_wire(self) -> dict[str, object]:
        return {
            "contentDigest": self.content_digest,
            "context": self.context.to_wire(),
            "identityDigest": self.identity_digest,
            "observedAt": self.observed_at.isoformat(),
            "proofId": self.proof_id,
            "resourceKind": self.resource_kind,
            "resourceRef": self.resource_ref,
            "stateRoot": self.state_root,
            "workspaceGeneration": self.workspace_generation,
            "workspaceRef": self.workspace_ref,
        }


@dataclass(frozen=True, slots=True)
class AbsenceProof:
    proof_id: str
    resource_ref: str
    nearest_existing_parent_ref: str
    parent_identity_digest: str
    workspace_ref: str
    workspace_generation: int
    state_root: str
    context: ProofContext
    observed_at: datetime
    proof_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _require_nonempty(self.proof_id, name="proof_id")
        require_canonical_workspace_resource_ref(self.resource_ref)
        require_canonical_workspace_resource_ref(self.nearest_existing_parent_ref)
        require_canonical_workspace_resource_ref(self.workspace_ref)
        if type(self.workspace_generation) is not int or self.workspace_generation < 0:
            raise ValueError("workspace_generation must be a non-negative integer")
        _require_digest(self.parent_identity_digest, name="parent_identity_digest")
        _require_digest(self.state_root, name="state_root")
        _require_aware(self.observed_at, name="observed_at")
        object.__setattr__(self, "proof_digest", _digest_json(self.to_wire()))

    def to_wire(self) -> dict[str, object]:
        return {
            "context": self.context.to_wire(),
            "nearestExistingParentRef": self.nearest_existing_parent_ref,
            "observedAt": self.observed_at.isoformat(),
            "parentIdentityDigest": self.parent_identity_digest,
            "proofId": self.proof_id,
            "resourceRef": self.resource_ref,
            "stateRoot": self.state_root,
            "workspaceGeneration": self.workspace_generation,
            "workspaceRef": self.workspace_ref,
        }


@dataclass(frozen=True, slots=True)
class MutationEntry:
    operation: MutationOperation
    resource_ref: str
    after_content: bytes | None = None
    proof_digest: str | None = None
    mode: int = 0o644

    def __post_init__(self) -> None:
        require_canonical_workspace_resource_ref(self.resource_ref)
        if self.proof_digest is not None:
            _require_digest(self.proof_digest, name="proof_digest")
        if type(self.mode) is not int or self.mode < 0 or self.mode > 0o777:
            raise ValueError("mode must contain ordinary permission bits")
        file_write = self.operation in {
            MutationOperation.CREATE_FILE,
            MutationOperation.REPLACE_FILE,
        }
        if file_write and type(self.after_content) is not bytes:
            raise ValueError("file writes require immutable after_content bytes")
        if not file_write and self.after_content is not None:
            raise ValueError("non-file writes cannot carry after_content")

    @property
    def after_content_digest(self) -> str | None:
        if self.after_content is None:
            return None
        return _digest_bytes(self.after_content)

    def to_wire(self, *, staged_name: str | None = None) -> dict[str, object]:
        return {
            "afterContentDigest": self.after_content_digest,
            "afterSize": None if self.after_content is None else len(self.after_content),
            "mode": self.mode,
            "operation": self.operation.value,
            "proofDigest": self.proof_digest,
            "resourceRef": self.resource_ref,
            "stagedName": staged_name,
        }


@dataclass(frozen=True, slots=True)
class MutationPlan:
    transaction_id: str
    action_id: str
    attempt_id: str
    workspace_ref: str
    workspace_generation: int
    state_root_before: str
    entries: tuple[MutationEntry, ...]
    read_proofs: tuple[ReadProof, ...]
    absence_proofs: tuple[AbsenceProof, ...]
    plan_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("transaction_id", "action_id", "attempt_id"):
            _require_nonempty(getattr(self, name), name=name)
        require_canonical_workspace_resource_ref(self.workspace_ref)
        if type(self.workspace_generation) is not int or self.workspace_generation < 0:
            raise ValueError("workspace_generation must be non-negative")
        _require_digest(self.state_root_before, name="state_root_before")
        if not self.entries or len(self.entries) > _MAX_PLAN_ENTRIES:
            raise InvalidWorkspacePlan("mutation plan entry count is outside policy")
        refs = tuple(entry.resource_ref for entry in self.entries)
        if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
            raise InvalidWorkspacePlan("mutation entries must have unique canonical order")
        total = sum(len(entry.after_content or b"") for entry in self.entries)
        if total > _MAX_STAGED_BYTES:
            raise InvalidWorkspacePlan("mutation plan exceeds the staged byte quota")
        object.__setattr__(self, "plan_digest", _digest_json(self.to_wire()))

    def to_wire(self) -> dict[str, object]:
        return {
            "absenceProofs": [
                proof.to_wire() | {"proofDigest": proof.proof_digest}
                for proof in self.absence_proofs
            ],
            "actionId": self.action_id,
            "attemptId": self.attempt_id,
            "entries": [entry.to_wire() for entry in self.entries],
            "readProofs": [
                proof.to_wire() | {"proofDigest": proof.proof_digest} for proof in self.read_proofs
            ],
            "stateRootBefore": self.state_root_before,
            "transactionId": self.transaction_id,
            "workspaceGeneration": self.workspace_generation,
            "workspaceRef": self.workspace_ref,
        }


@dataclass(frozen=True, slots=True)
class StagedWorkspaceEntry:
    operation: MutationOperation
    resource_ref: str
    staged_path: Path | None
    before_identity_digest: str | None
    before_content_digest: str | None
    after_content_digest: str | None
    mode: int


@dataclass(frozen=True, slots=True)
class StagedWorkspaceTransaction:
    transaction_id: str
    workspace_ref: str
    workspace_generation: int
    fencing_token: int
    manifest_path: Path
    manifest_digest: str
    plan_digest: str
    entries: tuple[StagedWorkspaceEntry, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceJournalSnapshot:
    workspace_ref: str
    generation: int
    state_root: str
    compare_version: int
    publication_state: PublicationState
    active_commit_id: str | None
    pending_generation: int | None
    pending_state_root: str | None
    active_fencing_token: int | None


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        data = _canonical_json(payload)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def _locked_file(path: Path) -> Iterator[None]:
    if fcntl is None:
        raise UnsupportedWorkspacePlatform("workspace publication requires POSIX flock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@runtime_checkable
class WorkspaceJournalPort(Protocol):
    def ensure_workspace(self, workspace_ref: str, state_root: str) -> None: ...

    def workspace_snapshot(self, workspace_ref: str) -> WorkspaceJournalSnapshot: ...

    def record_proof(self, proof: ReadProof | AbsenceProof) -> None: ...

    def contains_proof(self, proof_digest: str) -> bool: ...

    def record_staged(self, staged: StagedWorkspaceTransaction) -> None: ...


class DurableLocalWorkspaceJournal:
    """Small fsyncing JSON journal for isolated conformance and restart tests."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self._state_path = self.root / "workspace-journal.json"
        self._lock_path = self.root / "workspace-journal.lock"

    @staticmethod
    def _empty() -> dict[str, object]:
        return {
            "events": [],
            "proofs": {},
            "schemaVersion": 1,
            "transactions": {},
            "workspace": None,
        }

    def _load_unlocked(self) -> dict[str, object]:
        if not self._state_path.exists():
            return self._empty()
        loaded = json.loads(self._state_path.read_text(encoding="utf-8"))
        if type(loaded) is not dict or loaded.get("schemaVersion") != 1:
            raise WorkspaceWriterError("local workspace journal is malformed")
        return loaded

    def _save_unlocked(self, state: dict[str, object]) -> None:
        _atomic_write_json(self._state_path, state)

    def ensure_workspace(self, workspace_ref: str, state_root: str) -> None:
        require_canonical_workspace_resource_ref(workspace_ref)
        _require_digest(state_root, name="state_root")
        with _locked_file(self._lock_path):
            state = self._load_unlocked()
            workspace = state.get("workspace")
            if workspace is None:
                state["workspace"] = {
                    "activeCommitId": None,
                    "activeFencingToken": None,
                    "compareVersion": 0,
                    "generation": 0,
                    "pendingGeneration": None,
                    "pendingStateRoot": None,
                    "publicationState": PublicationState.READY.value,
                    "stateRoot": state_root,
                    "workspaceRef": workspace_ref,
                }
                self._save_unlocked(state)
                return
            if type(workspace) is not dict or workspace.get("workspaceRef") != workspace_ref:
                raise WorkspaceWriterError("journal belongs to a different workspace")

    def reset_initial_workspace(self, workspace_ref: str, state_root: str) -> None:
        """Adopt setup performed before any proof/transaction (test harness only)."""

        with _locked_file(self._lock_path):
            state = self._load_unlocked()
            if state.get("proofs") or state.get("transactions"):
                raise WorkspaceWriterError("cannot reset workspace after authority work exists")
            workspace = state.get("workspace")
            if type(workspace) is not dict or workspace.get("workspaceRef") != workspace_ref:
                raise WorkspaceWriterError("journal belongs to a different workspace")
            workspace["stateRoot"] = state_root
            self._save_unlocked(state)

    def workspace_snapshot(self, workspace_ref: str) -> WorkspaceJournalSnapshot:
        with _locked_file(self._lock_path):
            state = self._load_unlocked()
        workspace = state.get("workspace")
        if type(workspace) is not dict or workspace.get("workspaceRef") != workspace_ref:
            raise WorkspaceWriterError("workspace journal is not initialized")
        return WorkspaceJournalSnapshot(
            workspace_ref=workspace_ref,
            generation=int(workspace["generation"]),
            state_root=str(workspace["stateRoot"]),
            compare_version=int(workspace["compareVersion"]),
            publication_state=PublicationState(str(workspace["publicationState"])),
            active_commit_id=workspace.get("activeCommitId"),
            pending_generation=workspace.get("pendingGeneration"),
            pending_state_root=workspace.get("pendingStateRoot"),
            active_fencing_token=workspace.get("activeFencingToken"),
        )

    def record_proof(self, proof: ReadProof | AbsenceProof) -> None:
        with _locked_file(self._lock_path):
            state = self._load_unlocked()
            proofs = state.get("proofs")
            if type(proofs) is not dict:
                raise WorkspaceWriterError("journal proof index is malformed")
            wire = proof.to_wire() | {
                "proofDigest": proof.proof_digest,
                "proofType": "read" if isinstance(proof, ReadProof) else "absence",
            }
            prior = proofs.get(proof.proof_digest)
            if prior is not None and prior != wire:
                raise WorkspaceWriterError("proof digest collision")
            proofs[proof.proof_digest] = wire
            self._save_unlocked(state)

    def contains_proof(self, proof_digest: str) -> bool:
        with _locked_file(self._lock_path):
            state = self._load_unlocked()
        proofs = state.get("proofs")
        return type(proofs) is dict and proof_digest in proofs

    def record_staged(self, staged: StagedWorkspaceTransaction) -> None:
        with _locked_file(self._lock_path):
            state = self._load_unlocked()
            transactions = state.get("transactions")
            if type(transactions) is not dict:
                raise WorkspaceWriterError("journal transaction index is malformed")
            record = {
                "fencingToken": staged.fencing_token,
                "manifestDigest": staged.manifest_digest,
                "manifestPath": str(staged.manifest_path),
                "planDigest": staged.plan_digest,
                "state": PublicationState.STAGED.value,
                "transactionId": staged.transaction_id,
                "workspaceGeneration": staged.workspace_generation,
                "workspaceRef": staged.workspace_ref,
            }
            prior = transactions.get(staged.transaction_id)
            if prior is not None and prior != record:
                raise WorkspaceConflict("transaction id was already used")
            transactions[staged.transaction_id] = record
            events = state.get("events")
            if type(events) is not list:
                raise WorkspaceWriterError("journal event list is malformed")
            events.append(
                {
                    "eventType": "workspace.staged",
                    "manifestDigest": staged.manifest_digest,
                    "transactionId": staged.transaction_id,
                }
            )
            self._save_unlocked(state)


@dataclass(frozen=True, slots=True)
class WorkspaceLeaseGrant:
    workspace_ref: str
    owner_id: str
    fencing_token: int


@runtime_checkable
class WorkspaceLeasePort(Protocol):
    def assert_current(self, workspace_ref: str, fencing_token: int) -> None: ...

    def hold(
        self,
        workspace_ref: str,
        owner_id: str,
        fencing_token: int,
    ) -> Iterator[WorkspaceLeaseGrant]: ...


class LocalWorkspaceLeaseManager:
    """File-backed fence register plus a process-wide advisory lease."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _key(self, workspace_ref: str) -> str:
        return hashlib.sha256(workspace_ref.encode("utf-8")).hexdigest()

    def _fence_path(self, workspace_ref: str) -> Path:
        return self.root / f"{self._key(workspace_ref)}.fence.json"

    def _lock_path(self, workspace_ref: str) -> Path:
        return self.root / f"{self._key(workspace_ref)}.lease.lock"

    def set_current_fence(self, workspace_ref: str, fencing_token: int) -> None:
        require_canonical_workspace_resource_ref(workspace_ref)
        if type(fencing_token) is not int or fencing_token < 1:
            raise ValueError("fencing_token must be positive")
        with _locked_file(self._lock_path(workspace_ref)):
            path = self._fence_path(workspace_ref)
            current = 0
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                current = int(loaded["fencingToken"])
            if fencing_token < current:
                raise StaleWorkspaceFence("workspace fence cannot regress")
            _atomic_write_json(
                path,
                {"fencingToken": fencing_token, "workspaceRef": workspace_ref},
            )

    def assert_current(self, workspace_ref: str, fencing_token: int) -> None:
        path = self._fence_path(workspace_ref)
        if not path.exists():
            raise StaleWorkspaceFence("workspace fence has not been initialized")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if loaded.get("workspaceRef") != workspace_ref:
            raise StaleWorkspaceFence("workspace fence identity changed")
        if loaded.get("fencingToken") != fencing_token:
            raise StaleWorkspaceFence("execution token owns a stale workspace fence")

    @contextmanager
    def hold(
        self,
        workspace_ref: str,
        owner_id: str,
        fencing_token: int,
    ) -> Iterator[WorkspaceLeaseGrant]:
        with _locked_file(self._lock_path(workspace_ref)):
            self.assert_current(workspace_ref, fencing_token)
            grant = WorkspaceLeaseGrant(
                workspace_ref=workspace_ref,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
            yield grant
            self.assert_current(workspace_ref, fencing_token)


def _mode_bits(metadata: os.stat_result) -> int:
    return stat.S_IMODE(metadata.st_mode)


def _identity_digest(path: Path, metadata: os.stat_result) -> str:
    if stat.S_ISREG(metadata.st_mode):
        kind = "file"
        content_digest: str | None = _digest_bytes(path.read_bytes())
        size: int | None = metadata.st_size
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
        content_digest = None
        size = None
    else:
        raise InvalidWorkspacePlan("workspace proofs support regular files/directories only")
    return _digest_json(
        {
            "contentDigest": content_digest,
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "kind": kind,
            "links": metadata.st_nlink,
            "mode": _mode_bits(metadata),
            "size": size,
        }
    )


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    kind: Literal["file", "directory"]
    content: bytes | None
    mode: int

    @property
    def content_digest(self) -> str | None:
        return None if self.content is None else _digest_bytes(self.content)

    def root_wire(self, relative: str) -> dict[str, object]:
        return {
            "contentDigest": self.content_digest,
            "kind": self.kind,
            "mode": self.mode,
            "path": relative,
        }


def _tree_state_root(entries: Mapping[str, _TreeEntry]) -> str:
    return _digest_json(
        {
            "entries": [entries[path].root_wire(path) for path in sorted(entries)],
            "schemaId": "magi.workspace_state_root.v1",
        }
    )


class WorkspaceWriter:
    """Stage and later publish a complete workspace mutation plan."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        authority_root: Path,
        journal: WorkspaceJournalPort,
        leases: WorkspaceLeasePort,
        clock: WorkspaceClockPort,
        token_verifier: WorkspaceExecutionTokenVerifierPort,
    ) -> None:
        self.workspace_root = workspace_root.resolve(strict=True)
        if not self.workspace_root.is_dir():
            raise InvalidWorkspacePlan("workspace_root must be a directory")
        candidate_authority = authority_root.resolve(strict=False)
        try:
            candidate_authority.relative_to(self.workspace_root)
        except ValueError:
            pass
        else:
            raise InvalidWorkspacePlan("authority_root must be outside the live workspace")
        candidate_authority.mkdir(mode=0o700, parents=True, exist_ok=True)
        if candidate_authority.is_symlink() or not candidate_authority.is_dir():
            raise InvalidWorkspacePlan("authority_root must be a private directory")
        os.chmod(candidate_authority, 0o700)
        if candidate_authority.stat().st_dev != self.workspace_root.stat().st_dev:
            raise InvalidWorkspacePlan("authority_root must share the workspace filesystem")
        self.authority_root = candidate_authority
        self.journal = journal
        self.leases = leases
        self.clock = clock
        self.token_verifier = token_verifier
        self.workspace_ref = canonical_file_resource(self.workspace_root, self.workspace_root)
        self._transactions_root = self.authority_root / "transactions"
        self._transactions_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._transactions_root, 0o700)
        self.journal.ensure_workspace(self.workspace_ref, self._capture_state_root())

    def _capture_tree(self) -> dict[str, _TreeEntry]:
        result: dict[str, _TreeEntry] = {}
        for root, directory_names, file_names in os.walk(
            self.workspace_root,
            topdown=True,
            followlinks=False,
        ):
            directory_names.sort()
            file_names.sort()
            root_path = Path(root)
            for name in directory_names:
                path = root_path / name
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise InvalidWorkspacePlan("workspace state cannot include symlinks")
                if not stat.S_ISDIR(metadata.st_mode):
                    raise InvalidWorkspacePlan("workspace contains an unsupported directory entry")
                relative = path.relative_to(self.workspace_root).as_posix()
                result[relative] = _TreeEntry(
                    kind="directory",
                    content=None,
                    mode=_mode_bits(metadata),
                )
            for name in file_names:
                path = root_path / name
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise InvalidWorkspacePlan("workspace state supports regular files only")
                if metadata.st_nlink != 1:
                    raise InvalidWorkspacePlan("workspace state rejects hard-link aliases")
                relative = path.relative_to(self.workspace_root).as_posix()
                result[relative] = _TreeEntry(
                    kind="file",
                    content=path.read_bytes(),
                    mode=_mode_bits(metadata),
                )
        return result

    def _capture_state_root(self) -> str:
        return _tree_state_root(self._capture_tree())

    def refresh_initial_state_for_tests(self) -> None:
        reset = getattr(self.journal, "reset_initial_workspace", None)
        if reset is None:
            raise WorkspaceWriterError("journal does not expose test baseline reset")
        reset(self.workspace_ref, self._capture_state_root())

    def _relative_path(self, resource_ref: str) -> PurePosixPath:
        try:
            relative = workspace_relative_path(self.workspace_root, resource_ref)
        except CanonicalizationError as exc:
            raise InvalidWorkspacePlan("resource ref is not canonical for this workspace") from exc
        if relative == PurePosixPath("."):
            raise InvalidWorkspacePlan("the workspace root itself cannot be mutated")
        if relative.parts[0] in _PROTECTED_TOP_LEVEL:
            raise InvalidWorkspacePlan("mutation targets a protected workspace path")
        return relative

    def _live_path(self, resource_ref: str) -> Path:
        relative = self._relative_path(resource_ref)
        return self.workspace_root.joinpath(*relative.parts)

    def observe_read(self, path: str | Path, *, context: ProofContext) -> ReadProof:
        resource_ref = canonical_file_resource(self.workspace_root, path)
        live_path = self._live_path(resource_ref)
        metadata = live_path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
            raise InvalidWorkspacePlan("read proof rejects symlink/hard-link aliases")
        snapshot = self.journal.workspace_snapshot(self.workspace_ref)
        actual_root = self._capture_state_root()
        if actual_root != snapshot.state_root:
            raise WorkspaceConflict("workspace state root changed before read proof")
        if stat.S_ISREG(metadata.st_mode):
            kind: Literal["file", "directory"] = "file"
            content_digest: str | None = _digest_bytes(live_path.read_bytes())
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            content_digest = None
        else:
            raise InvalidWorkspacePlan("read proof supports regular files/directories only")
        proof = ReadProof(
            proof_id=f"read-{hashlib.sha256(resource_ref.encode()).hexdigest()[:24]}",
            resource_ref=resource_ref,
            resource_kind=kind,
            content_digest=content_digest,
            identity_digest=_identity_digest(live_path, metadata),
            workspace_ref=self.workspace_ref,
            workspace_generation=snapshot.generation,
            state_root=snapshot.state_root,
            context=context,
            observed_at=self.clock.now(),
        )
        self.journal.record_proof(proof)
        return proof

    def build_plan(
        self,
        *,
        transaction_id: str,
        action_id: str,
        attempt_id: str,
        entries: Sequence[MutationEntry],
        read_proofs: Sequence[ReadProof],
        absence_proofs: Sequence[AbsenceProof],
    ) -> MutationPlan:
        snapshot = self.journal.workspace_snapshot(self.workspace_ref)
        ordered_entries = tuple(sorted(entries, key=lambda entry: entry.resource_ref))
        return MutationPlan(
            transaction_id=transaction_id,
            action_id=action_id,
            attempt_id=attempt_id,
            workspace_ref=self.workspace_ref,
            workspace_generation=snapshot.generation,
            state_root_before=snapshot.state_root,
            entries=ordered_entries,
            read_proofs=tuple(read_proofs),
            absence_proofs=tuple(absence_proofs),
        )

    def _validate_token(
        self,
        token: WorkspaceExecutionToken,
        *,
        plan: MutationPlan,
    ) -> None:
        if not self.token_verifier.verify(token):
            raise WorkspaceConflict("execution token verification failed")
        now = self.clock.now()
        _require_aware(now, name="clock.now")
        if now < token.issued_at or now >= token.expires_at:
            raise WorkspaceConflict("execution token is not currently valid")
        if (
            token.action_id != plan.action_id
            or token.attempt_id != plan.attempt_id
            or token.workspace_ref != plan.workspace_ref
        ):
            raise WorkspaceConflict("execution token does not bind the mutation plan")
        self.leases.assert_current(self.workspace_ref, token.fencing_token)

    def _validate_plan_proofs(self, plan: MutationPlan) -> None:
        reads = {proof.proof_digest: proof for proof in plan.read_proofs}
        absences = {proof.proof_digest: proof for proof in plan.absence_proofs}
        if len(reads) != len(plan.read_proofs) or len(absences) != len(plan.absence_proofs):
            raise InvalidWorkspacePlan("proof digests must be unique")
        for proof in plan.read_proofs:
            if not self.journal.contains_proof(proof.proof_digest):
                raise InvalidWorkspacePlan("mutation proof is not durably recorded")
            if (
                proof.workspace_ref != plan.workspace_ref
                or proof.workspace_generation != plan.workspace_generation
                or proof.state_root != plan.state_root_before
            ):
                raise InvalidWorkspacePlan("mutation proof does not bind the plan snapshot")
        for absence_proof in plan.absence_proofs:
            if not self.journal.contains_proof(absence_proof.proof_digest):
                raise InvalidWorkspacePlan("mutation proof is not durably recorded")
            if (
                absence_proof.workspace_ref != plan.workspace_ref
                or absence_proof.workspace_generation != plan.workspace_generation
                or absence_proof.state_root != plan.state_root_before
            ):
                raise InvalidWorkspacePlan("mutation proof does not bind the plan snapshot")
        for entry in plan.entries:
            self._relative_path(entry.resource_ref)
            if entry.proof_digest is None:
                raise InvalidWorkspacePlan("every mutation requires an exact proof digest")
            if entry.operation in {
                MutationOperation.REPLACE_FILE,
                MutationOperation.DELETE_FILE,
                MutationOperation.DELETE_DIRECTORY,
            }:
                read_proof = reads.get(entry.proof_digest)
                if read_proof is None or read_proof.resource_ref != entry.resource_ref:
                    raise InvalidWorkspacePlan("existing target requires its exact read proof")
            else:
                entry_absence_proof = absences.get(entry.proof_digest)
                if (
                    entry_absence_proof is None
                    or entry_absence_proof.resource_ref != entry.resource_ref
                ):
                    raise InvalidWorkspacePlan("create target requires its exact absence proof")

    def _revalidate_read(self, proof: ReadProof) -> None:
        path = self._live_path(proof.resource_ref)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise WorkspaceConflict("read proof target no longer exists") from exc
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
            raise WorkspaceConflict("read proof target identity changed")
        if _identity_digest(path, metadata) != proof.identity_digest:
            raise WorkspaceConflict("read proof no longer matches the live target")
        if (
            proof.content_digest is not None
            and _digest_bytes(path.read_bytes()) != proof.content_digest
        ):
            raise WorkspaceConflict("read proof content changed")

    def _revalidate_absence(self, proof: AbsenceProof) -> None:
        if self._live_path(proof.resource_ref).exists():
            raise WorkspaceConflict("absence proof target now exists")
        parent = self._live_path(proof.nearest_existing_parent_ref)
        try:
            metadata = parent.lstat()
        except FileNotFoundError as exc:
            raise WorkspaceConflict("absence proof parent disappeared") from exc
        if _identity_digest(parent, metadata) != proof.parent_identity_digest:
            raise WorkspaceConflict("absence proof parent identity changed")

    def _revalidate_plan(self, plan: MutationPlan) -> None:
        snapshot = self.journal.workspace_snapshot(self.workspace_ref)
        if snapshot.publication_state is not PublicationState.READY:
            raise WorkspaceConflict("workspace generation is not ready")
        if (
            snapshot.generation != plan.workspace_generation
            or snapshot.state_root != plan.state_root_before
            or self._capture_state_root() != plan.state_root_before
        ):
            raise WorkspaceConflict("workspace generation or state root changed")
        for proof in plan.read_proofs:
            self._revalidate_read(proof)
        for absence_proof in plan.absence_proofs:
            self._revalidate_absence(absence_proof)

    def _manifest_payload(
        self,
        plan: MutationPlan,
        staged_names: Mapping[str, str | None],
        fencing_token: int,
    ) -> dict[str, object]:
        return {
            "fencingToken": fencing_token,
            "plan": plan.to_wire(),
            "planDigest": plan.plan_digest,
            "schemaId": "magi.workspace_staging_manifest.v1",
            "stagedEntries": [
                entry.to_wire(staged_name=staged_names[entry.resource_ref])
                for entry in plan.entries
            ],
        }

    def stage(
        self,
        *,
        plan: MutationPlan,
        execution_token: WorkspaceExecutionToken,
    ) -> StagedWorkspaceTransaction:
        self._validate_token(execution_token, plan=plan)
        self._validate_plan_proofs(plan)
        self._revalidate_plan(plan)
        transaction_root = self._transactions_root / plan.transaction_id
        try:
            transaction_root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkspaceConflict("transaction staging area already exists") from exc
        staged_names: dict[str, str | None] = {}
        staged_entries: list[StagedWorkspaceEntry] = []
        try:
            for index, entry in enumerate(plan.entries):
                read_proof = next(
                    (
                        proof
                        for proof in plan.read_proofs
                        if proof.proof_digest == entry.proof_digest
                    ),
                    None,
                )
                staged_path: Path | None = None
                staged_name: str | None = None
                if entry.after_content is not None:
                    staged_name = f"{index:04d}.blob"
                    staged_path = transaction_root / staged_name
                    descriptor = os.open(
                        staged_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    try:
                        view = memoryview(entry.after_content)
                        while view:
                            written = os.write(descriptor, view)
                            view = view[written:]
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    if _digest_bytes(staged_path.read_bytes()) != entry.after_content_digest:
                        raise WorkspaceWriterError("staged content digest changed")
                staged_names[entry.resource_ref] = staged_name
                staged_entries.append(
                    StagedWorkspaceEntry(
                        operation=entry.operation,
                        resource_ref=entry.resource_ref,
                        staged_path=staged_path,
                        before_identity_digest=(
                            None if read_proof is None else read_proof.identity_digest
                        ),
                        before_content_digest=(
                            None if read_proof is None else read_proof.content_digest
                        ),
                        after_content_digest=entry.after_content_digest,
                        mode=entry.mode,
                    )
                )
            manifest_payload = self._manifest_payload(
                plan,
                staged_names,
                execution_token.fencing_token,
            )
            manifest_bytes = _canonical_json(manifest_payload)
            manifest_path = transaction_root / "manifest.json"
            descriptor = os.open(
                manifest_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                view = memoryview(manifest_bytes)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            directory = os.open(
                transaction_root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            staged = StagedWorkspaceTransaction(
                transaction_id=plan.transaction_id,
                workspace_ref=plan.workspace_ref,
                workspace_generation=plan.workspace_generation,
                fencing_token=execution_token.fencing_token,
                manifest_path=manifest_path,
                manifest_digest=_digest_bytes(manifest_bytes),
                plan_digest=plan.plan_digest,
                entries=tuple(staged_entries),
            )
            self.journal.record_staged(staged)
            return staged
        except BaseException:
            # The staging directory is intentionally retained when a durable
            # journal append may have happened.  Recovery validates the
            # manifest and decides whether it is safe to discard.
            raise


__all__ = [
    "AbsenceProof",
    "DurableLocalWorkspaceJournal",
    "InvalidWorkspacePlan",
    "LocalWorkspaceLeaseManager",
    "MutationEntry",
    "MutationOperation",
    "MutationPlan",
    "ProofContext",
    "PublicationState",
    "ReadProof",
    "StagedWorkspaceEntry",
    "StagedWorkspaceTransaction",
    "StaleWorkspaceFence",
    "UnsupportedWorkspacePlatform",
    "WorkspaceClockPort",
    "WorkspaceConflict",
    "WorkspaceExecutionToken",
    "WorkspaceExecutionTokenVerifierPort",
    "WorkspaceJournalPort",
    "WorkspaceLeaseGrant",
    "WorkspaceLeasePort",
    "WorkspaceWriter",
    "WorkspaceWriterError",
]
