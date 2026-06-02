from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
)

from .eval_capture import EvalObservation, EvalValidatorResult


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_FAILURE_STATUSES = frozenset({"fail", "blocked", "error"})
_SHA256_REF_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_CLUSTER_ID_RE = re.compile(r"^failure-cluster:[a-f0-9]{16,64}$")
_SAFE_VALIDATOR_REF_RE = re.compile(r"^validator:[A-Za-z0-9_.:@=-]{1,191}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SAFE_TERMINAL_STATES = frozenset({"passed", "failed", "blocked", "cancelled", "timeout", "error"})


class FailureCluster(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementFailureCluster.v1"] = Field(
        default="selfImprovementFailureCluster.v1",
        alias="schemaVersion",
    )
    cluster_id: str = Field(alias="clusterId")
    failure_signature_digest: str = Field(alias="failureSignatureDigest")
    observation_digest_refs: tuple[str, ...] = Field(alias="observationDigestRefs")
    validator_ids: tuple[str, ...] = Field(alias="validatorIds")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    terminal_states: tuple[str, ...] = Field(alias="terminalStates")
    occurrence_count: int = Field(alias="occurrenceCount")

    @field_validator("cluster_id")
    @classmethod
    def _validate_cluster_id(cls, value: str) -> str:
        if not _CLUSTER_ID_RE.fullmatch(value) or sanitize_public_text(value) != value:
            raise ValueError("clusterId must be a safe failure-cluster digest ref")
        return value

    @field_validator("failure_signature_digest")
    @classmethod
    def _validate_failure_signature_digest(cls, value: str) -> str:
        if not _SHA256_REF_RE.fullmatch(value):
            raise ValueError("failureSignatureDigest must be sha256:<64 lowercase hex>")
        return value

    @field_validator("observation_digest_refs", mode="before")
    @classmethod
    def _validate_observation_digest_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("observationDigestRefs must not be empty")
        for ref in refs:
            if not _SHA256_REF_RE.fullmatch(ref):
                raise ValueError("observationDigestRefs must be sha256 digest refs")
        return refs

    @field_validator("validator_ids", mode="before")
    @classmethod
    def _validate_validator_ids(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        for ref in refs:
            if (
                not _SAFE_VALIDATOR_REF_RE.fullmatch(ref)
                or has_unsafe_marker(ref)
                or sanitize_public_text(ref) != ref
            ):
                raise ValueError("validatorIds must be safe validator refs")
        return refs

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        codes = _string_tuple(value)
        for code in codes:
            if (
                not _SAFE_REASON_RE.fullmatch(code)
                or has_unsafe_marker(code)
                or sanitize_public_text(code) != code
            ):
                raise ValueError("reasonCodes must be safe reason codes")
        return codes

    @field_validator("terminal_states", mode="before")
    @classmethod
    def _validate_terminal_states(cls, value: object) -> tuple[str, ...]:
        states = _string_tuple(value)
        for state in states:
            if state not in _SAFE_TERMINAL_STATES:
                raise ValueError("terminalStates must be known eval terminal states")
        return states

    @model_validator(mode="after")
    def _validate_count(self) -> Self:
        if self.occurrence_count != len(self.observation_digest_refs):
            raise ValueError("occurrenceCount must match observationDigestRefs")
        expected_cluster_id = (
            "failure-cluster:" + self.failure_signature_digest.removeprefix("sha256:")[:32]
        )
        if self.cluster_id != expected_cluster_id:
            raise ValueError("clusterId must bind failureSignatureDigest")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        raise ValueError("model_copy is disabled for FailureCluster")

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for FailureCluster")


class FailureClusterSet(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementFailureClusterSet.v1"] = Field(
        default="selfImprovementFailureClusterSet.v1",
        alias="schemaVersion",
    )
    cluster_set_digest: str = Field(alias="clusterSetDigest")
    clusters: tuple[FailureCluster, ...]
    observation_count: int = Field(alias="observationCount")
    failure_count: int = Field(alias="failureCount")

    @model_validator(mode="after")
    def _validate_digest(self) -> Self:
        payload = self.model_dump(by_alias=True, exclude={"cluster_set_digest"})
        if self.cluster_set_digest != canonical_digest(payload):
            raise ValueError("clusterSetDigest mismatch")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        clusters = tuple(
            item
            if isinstance(item, FailureCluster)
            else FailureCluster.model_validate(item)
            for item in _object_tuple(values.get("clusters"))
        )
        payload = {
            "schemaVersion": "selfImprovementFailureClusterSet.v1",
            "clusters": tuple(cluster.model_dump(by_alias=True) for cluster in clusters),
            "observationCount": int(values.get("observationCount", values.get("observation_count", 0))),
            "failureCount": int(values.get("failureCount", values.get("failure_count", 0))),
        }
        payload["clusterSetDigest"] = canonical_digest(payload)
        return cls.model_validate(payload)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        raise ValueError("model_copy is disabled for FailureClusterSet")

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for FailureClusterSet")


class FailureClusterer:
    def cluster(self, observations: Iterable[EvalObservation]) -> FailureClusterSet:
        ordered = tuple(observations)
        grouped: dict[str, list[EvalObservation]] = defaultdict(list)
        for observation in ordered:
            if _is_failure_observation(observation):
                grouped[observation.failure_signature_digest].append(observation)

        clusters = tuple(
            _build_cluster(signature_digest, grouped[signature_digest])
            for signature_digest in sorted(grouped)
        )
        payload = {
            "schemaVersion": "selfImprovementFailureClusterSet.v1",
            "clusters": tuple(cluster.model_dump(by_alias=True) for cluster in clusters),
            "observationCount": len(ordered),
            "failureCount": sum(len(items) for items in grouped.values()),
        }
        return FailureClusterSet.model_validate(
            payload | {"clusterSetDigest": canonical_digest(payload)}
        )


def _is_failure_observation(observation: EvalObservation) -> bool:
    if observation.terminal_state != "passed":
        return True
    return any(result.status in _FAILURE_STATUSES for result in observation.validator_results)


def _build_cluster(
    failure_signature_digest: str,
    observations: list[EvalObservation],
) -> FailureCluster:
    validator_results = tuple(
        result
        for observation in observations
        for result in _failure_validator_results(observation.validator_results)
    )
    payload = {
        "schemaVersion": "selfImprovementFailureCluster.v1",
        "clusterId": "failure-cluster:" + failure_signature_digest.removeprefix("sha256:")[:32],
        "failureSignatureDigest": failure_signature_digest,
        "observationDigestRefs": tuple(
            sorted(observation.observation_digest for observation in observations)
        ),
        "validatorIds": tuple(sorted({result.validator_id for result in validator_results})),
        "reasonCodes": tuple(
            sorted({reason for result in validator_results for reason in result.reason_codes})
        ),
        "terminalStates": tuple(sorted({observation.terminal_state for observation in observations})),
        "occurrenceCount": len(observations),
    }
    return FailureCluster.model_validate(payload)


def _failure_validator_results(
    validator_results: tuple[EvalValidatorResult, ...],
) -> tuple[EvalValidatorResult, ...]:
    failed = tuple(result for result in validator_results if result.status in _FAILURE_STATUSES)
    return failed or validator_results


def _object_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple | list):
        return tuple(str(item) for item in value)
    return (str(value),)


__all__ = [
    "FailureCluster",
    "FailureClusterSet",
    "FailureClusterer",
]
