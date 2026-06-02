from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

_PROOF_KEY = "openmagi.sessionContinuityProof"
_PROOF_ISSUER = "openmagi.python.sessionContinuityBoundary"
_PROOF_VERSION = "v1"
_CONTINUITY_SOURCES = frozenset(
    {
        "ts_transcript_read_committed",
        "memory_recall_metadata",
    }
)


def attach_session_continuity_proof(event: object) -> object:
    metadata = dict(_event_metadata(event) or {})
    metadata[_PROOF_KEY] = {
        "version": _PROOF_VERSION,
        "issuer": _PROOF_ISSUER,
        "eventDigest": session_continuity_event_digest(event),
    }
    setattr(event, "custom_metadata", metadata)
    return event


def attach_session_continuity_batch_proof(events: Sequence[object]) -> tuple[object, ...]:
    proved_events = tuple(attach_session_continuity_proof(event) for event in events)
    event_digests = [session_continuity_event_digest(event) for event in proved_events]
    batch_digest = _batch_digest(event_digests)
    batch_size = len(proved_events)
    for index, event in enumerate(proved_events):
        metadata = dict(_event_metadata(event) or {})
        proof = dict(metadata.get(_PROOF_KEY) or {})
        proof.update(
            {
                "batchDigest": batch_digest,
                "batchIndex": index,
                "batchSize": batch_size,
            }
        )
        metadata[_PROOF_KEY] = proof
        setattr(event, "custom_metadata", metadata)
    return proved_events


def has_session_continuity_marker(event: object) -> bool:
    marker = session_continuity_marker(event)
    return marker is not None and marker.get("source") in _CONTINUITY_SOURCES


def session_continuity_marker(event: object) -> Mapping[str, object] | None:
    metadata = _event_metadata(event)
    if not isinstance(metadata, Mapping):
        return None
    marker = metadata.get("openmagi.sessionContinuity")
    return marker if isinstance(marker, Mapping) else None


def session_continuity_source(event: object) -> object:
    marker = session_continuity_marker(event)
    return marker.get("source") if marker is not None else None


def session_continuity_kind(event: object) -> object:
    marker = session_continuity_marker(event)
    return marker.get("kind") if marker is not None else None


def session_continuity_event_digest(event: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            _session_continuity_event_payload(event),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def validate_session_continuity_proof(event: object) -> bool:
    if not has_session_continuity_marker(event):
        return False
    metadata = _event_metadata(event)
    if not isinstance(metadata, Mapping):
        return False
    proof = metadata.get(_PROOF_KEY)
    if not isinstance(proof, Mapping):
        return False
    return (
        proof.get("version") == _PROOF_VERSION
        and proof.get("issuer") == _PROOF_ISSUER
        and proof.get("eventDigest") == session_continuity_event_digest(event)
    )


def latest_valid_compacted_batch(events: Sequence[object]) -> tuple[object, ...]:
    valid_events = [event for event in events if validate_session_continuity_proof(event)]
    for event in reversed(valid_events):
        if session_continuity_kind(event) != "compaction_boundary":
            continue
        batch_digest = _batch_digest_for(event)
        if batch_digest is None:
            continue
        batch = _latest_ordered_batch(valid_events, batch_digest=batch_digest)
        if batch:
            return batch
    return ()


def has_valid_compaction_boundary(events: Sequence[object]) -> bool:
    return any(
        validate_session_continuity_proof(event)
        and session_continuity_kind(event) == "compaction_boundary"
        for event in events
    )


def _session_continuity_event_payload(event: object) -> dict[str, object]:
    content = getattr(event, "content", None)
    metadata = dict(_event_metadata(event) or {})
    metadata.pop(_PROOF_KEY, None)
    return {
        "author": getattr(event, "author", None),
        "invocationId": getattr(event, "invocation_id", None),
        "contentRole": getattr(content, "role", None),
        "parts": _content_part_payloads(event),
        "metadata": metadata,
    }


def _content_part_payloads(event: object) -> list[dict[str, object]]:
    content = getattr(event, "content", None)
    parts = list(getattr(content, "parts", ()) or ())
    payloads: list[dict[str, object]] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            payloads.append({"text": text})
            continue
        model_dump = getattr(part, "model_dump", None)
        if callable(model_dump):
            payloads.append({"part": model_dump(mode="json", exclude_none=True)})
            continue
        payloads.append({"part": str(part)})
    return payloads


def _event_metadata(event: object) -> Mapping[str, object] | None:
    metadata = getattr(event, "custom_metadata", None)
    return metadata if isinstance(metadata, Mapping) else None


def _proof(event: object) -> Mapping[str, object] | None:
    metadata = _event_metadata(event)
    if not isinstance(metadata, Mapping):
        return None
    proof = metadata.get(_PROOF_KEY)
    return proof if isinstance(proof, Mapping) else None


def _batch_digest_for(event: object) -> str | None:
    proof = _proof(event)
    value = proof.get("batchDigest") if proof is not None else None
    return value if isinstance(value, str) else None


def _batch_index(event: object) -> int | None:
    proof = _proof(event)
    value = proof.get("batchIndex") if proof is not None else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _batch_size(event: object) -> int | None:
    proof = _proof(event)
    value = proof.get("batchSize") if proof is not None else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _validated_batch(events: Sequence[object], *, expected_digest: str) -> bool:
    if not events:
        return False
    batch_size = _batch_size(events[0])
    if batch_size is None or batch_size <= 0 or len(events) != batch_size:
        return False
    indexed: dict[int, object] = {}
    for event in events:
        if _batch_size(event) != batch_size or _batch_digest_for(event) != expected_digest:
            return False
        index = _batch_index(event)
        if index is None or index < 0 or index >= batch_size or index in indexed:
            return False
        indexed[index] = event
    if sorted(indexed) != list(range(batch_size)):
        return False
    ordered = [indexed[index] for index in range(batch_size)]
    event_digests = [session_continuity_event_digest(event) for event in ordered]
    return _batch_digest(event_digests) == expected_digest


def _latest_ordered_batch(
    events: Sequence[object],
    *,
    batch_digest: str,
) -> tuple[object, ...]:
    candidates = [event for event in events if _batch_digest_for(event) == batch_digest]
    for start_position in range(len(candidates) - 1, -1, -1):
        event = candidates[start_position]
        batch_size = _batch_size(event)
        if batch_size is None or batch_size <= 0:
            continue
        start = _batch_index(event)
        if start != 0:
            continue
        window: list[object] = []
        next_index = 0
        seen_digests: set[tuple[int, str]] = set()
        for candidate in candidates[start_position:]:
            index = _batch_index(candidate)
            if index != next_index or _batch_size(candidate) != batch_size:
                continue
            event_digest = session_continuity_event_digest(candidate)
            dedupe_key = (index, event_digest)
            if dedupe_key in seen_digests:
                continue
            window.append(candidate)
            seen_digests.add(dedupe_key)
            next_index += 1
            if next_index == batch_size:
                break
        if len(window) == batch_size and _validated_batch(
            window,
            expected_digest=batch_digest,
        ):
            return tuple(window)
    return ()


def _batch_digest(event_digests: Sequence[str]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "version": _PROOF_VERSION,
                "eventDigests": list(event_digests),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "attach_session_continuity_batch_proof",
    "attach_session_continuity_proof",
    "has_valid_compaction_boundary",
    "has_session_continuity_marker",
    "latest_valid_compacted_batch",
    "session_continuity_event_digest",
    "session_continuity_kind",
    "session_continuity_marker",
    "session_continuity_source",
    "validate_session_continuity_proof",
]
