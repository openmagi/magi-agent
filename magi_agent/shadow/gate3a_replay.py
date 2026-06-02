from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from magi_agent.shadow.gate3a_bundle import (
    Gate3ARecordedBundle,
    _validated_gate3a_recorded_bundle_snapshot,
)
from magi_agent.shadow.gate3a_report import (
    Gate3AComparisonReport,
    Gate3AParityStatus,
    build_gate3a_comparison_report,
    sanitize_gate3a_report_failure,
)


_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data|workspace|transcripts?|buckets?|db|database)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|"
    r"infra[\\/]k8s|infra[\\/]docker[\\/]provisioning-worker|deploy\.sh|"
    r"(?:^|[\\/])k3s(?:[\\/]|$)|(?:^|[\\/])[.]kube(?:[\\/]|$)|"
    r"(?:^|[\\/])secrets?(?:[\\/]|$)|bot-[A-Za-z0-9_-]+|"
    r"(?:^|[\\/])missions?(?:[\\/]|$)|(?:^|[\\/])schedulers?(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:mission|scheduler)-store(?:[\\/]|$)|"
    r"(?:^|[\\/])store(?:[\\/]|$)",
    re.IGNORECASE,
)
_URI_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_PRODUCTION_HOST_RE = re.compile(r"\bmagi\.pro\b", re.IGNORECASE)
_LOCAL_ADK_PRIMITIVES = ("Agent", "Runner", "Event")
_RUNNER_ATTACHMENT_FLAGS = (
    "live_capture_attached",
    "production_route_attached",
    "production_storage_attached",
    "user_visible_output_attached",
    "telegram_attached",
    "tool_side_effects_attached",
    "evidence_block_mode_attached",
)


class Gate3AReplayError(RuntimeError):
    pass


class Gate3ALocalRunner(Protocol):
    async def collect_events(self, bundle: Gate3ARecordedBundle) -> list[object]:
        ...


class Gate3ALocalReplayRunner:
    adk_primitives = _LOCAL_ADK_PRIMITIVES
    local_only = True
    live_capture_attached = False
    production_route_attached = False
    production_storage_attached = False
    user_visible_output_attached = False
    telegram_attached = False
    tool_side_effects_attached = False
    evidence_block_mode_attached = False

    def __init__(
        self,
        events: Iterable[object] = (),
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._events = tuple(events)
        self._failure = failure
        self.called_with: Gate3ARecordedBundle | None = None

    async def collect_events(self, bundle: Gate3ARecordedBundle) -> list[object]:
        self.called_with = bundle
        if self._failure is not None:
            raise self._failure
        return list(self._events)


class RecordedOutputToolPolicy:
    def __init__(self) -> None:
        self.live_dispatch_attempts = 0

    async def resolve_recorded_tool_output(
        self,
        *,
        tool_call_id: str,
        bundle: Gate3ARecordedBundle,
    ) -> Mapping[str, object] | None:
        for tool_result in bundle.recorded_tool_results:
            if tool_result.tool_call_id == tool_call_id:
                return tool_result.output_metadata
        return None

    async def dispatch_live_tool(self, *args: object, **kwargs: object) -> None:
        self.live_dispatch_attempts += 1
        raise Gate3AReplayError("Gate 3A replay must not dispatch live tools")


def validate_gate3a_local_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    path_text = str(path)
    _reject_unsafe_gate3a_path_text(path_text)
    candidate = Path(path)
    _reject_unsafe_gate3a_path_text(str(candidate.resolve(strict=False)))
    return candidate


def _reject_unsafe_gate3a_path_text(path_text: str) -> None:
    if (
        _URI_SCHEME_RE.search(path_text)
        or _PRODUCTION_HOST_RE.search(path_text)
        or _PRODUCTION_PATH_RE.search(path_text)
    ):
        raise ValueError("Gate 3A paths must be local-only and non-production")


async def run_gate3a_recorded_replay_async(
    bundle: Gate3ARecordedBundle,
    *,
    local_runner: Gate3ALocalRunner,
    output_dir: str | Path | None = None,
    tool_policy: RecordedOutputToolPolicy | None = None,
) -> Gate3AComparisonReport:
    try:
        bundle = _validated_gate3a_recorded_bundle_snapshot(bundle)
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise Gate3AReplayError("invalid Gate 3A recorded bundle") from exc

    try:
        output_path = validate_gate3a_report_output_dir(output_dir)
    except ValueError as exc:
        raise Gate3AReplayError("invalid Gate 3A report output directory") from exc
    try:
        _require_local_adk_runner(local_runner)
    except ValueError as exc:
        raise Gate3AReplayError("invalid Gate 3A local ADK runner boundary") from exc

    try:
        _require_recorded_output_tool_policy(tool_policy)
    except ValueError as exc:
        raise Gate3AReplayError("invalid Gate 3A recorded output policy boundary") from exc

    try:
        runner_events = tuple(await local_runner.collect_events(bundle))
    except Exception as exc:
        report = _failure_report(
            bundle,
            "runner_failure",
            sanitize_gate3a_report_failure(str(exc)),
        )
        _write_gate3a_report_artifact(report, output_path)
        return report

    event_projection = _compare_event_projection(bundle, runner_events)
    transcript_projection = _compare_transcript_projection(
        bundle,
        runner_events,
        event_projection=event_projection,
    )
    tool_projection = await _compare_recorded_tool_projection(
        bundle,
        runner_events,
    )
    if tool_projection in {"extra", "missing"} and event_projection == "pass":
        event_projection = tool_projection

    report = build_gate3a_comparison_report(
        bundle_id=bundle.bundle_id,
        shadow_run_id=f"shadow_local_{uuid4().hex[:12]}",
        recipe_snapshot_id=bundle.recipe.recipe_snapshot_id,
        event_projection=event_projection,
        transcript_projection=transcript_projection,
        sse_projection="not_applicable",
        tool_projection=tool_projection,
    )
    _write_gate3a_report_artifact(report, output_path)
    return report


def run_gate3a_recorded_replay(
    bundle: Gate3ARecordedBundle,
    *,
    local_runner: Gate3ALocalRunner,
    output_dir: str | Path | None = None,
    tool_policy: RecordedOutputToolPolicy | None = None,
) -> Gate3AComparisonReport:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            run_gate3a_recorded_replay_async(
                bundle,
                local_runner=local_runner,
                output_dir=output_dir,
                tool_policy=tool_policy,
            )
        )
    raise RuntimeError("run_gate3a_recorded_replay cannot run inside an active event loop")


def _event_id(event: object) -> str | None:
    if isinstance(event, Mapping):
        value = event.get("event_id") or event.get("eventId") or event.get("id")
    else:
        value = (
            getattr(event, "event_id", None)
            or getattr(event, "eventId", None)
            or getattr(event, "id", None)
        )
    return value if isinstance(value, str) and value.strip() else None


def _tool_call_id(event: object) -> str | None:
    if isinstance(event, Mapping):
        value = (
            event.get("tool_call_id")
            or event.get("toolCallId")
            or _event_metadata_value(event, "tool_call_id", "toolCallId")
        )
    else:
        value = (
            getattr(event, "tool_call_id", None)
            or getattr(event, "toolCallId", None)
            or _event_metadata_value(event, "tool_call_id", "toolCallId")
        )
    return value if isinstance(value, str) and value.strip() else None


def _event_metadata_value(event: object, *keys: str) -> object:
    metadata: object
    if isinstance(event, Mapping):
        metadata = event.get("custom_metadata") or event.get("customMetadata") or {}
    else:
        metadata = getattr(event, "custom_metadata", None) or getattr(
            event,
            "customMetadata",
            None,
        ) or {}
    if not isinstance(metadata, Mapping):
        return None
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return value
    return None


def _record_event_ids(bundle: Gate3ARecordedBundle) -> tuple[str, ...]:
    event_ids: list[str] = []
    for event in bundle.agent_events:
        payload = event.as_dict()
        value = payload.get("eventId") or payload.get("event_id") or payload.get("id")
        if isinstance(value, str) and value.strip():
            event_ids.append(value)
    return tuple(event_ids)


def _compare_event_projection(
    bundle: Gate3ARecordedBundle,
    runner_events: Iterable[object],
) -> Gate3AParityStatus:
    recorded = _record_event_ids(bundle)
    actual = tuple(event_id for event in runner_events if (event_id := _event_id(event)))
    if recorded == actual:
        return "pass"
    recorded_set = set(recorded)
    actual_set = set(actual)
    if actual_set - recorded_set:
        return "extra"
    if recorded_set - actual_set:
        return "missing"
    return "mismatch"


def _record_transcript_ids(bundle: Gate3ARecordedBundle) -> tuple[str, ...]:
    transcript_ids: list[str] = []
    for entry in bundle.transcript_entries:
        payload = entry.as_dict()
        value = (
            payload.get("entryId")
            or payload.get("entry_id")
            or payload.get("transcriptId")
            or payload.get("transcript_id")
            or payload.get("id")
        )
        if isinstance(value, str) and value.strip():
            transcript_ids.append(value)
    return tuple(transcript_ids)


def _event_transcript_refs(event: object) -> tuple[str, ...]:
    values: list[str] = []
    candidate_values = (
        _event_field_value(
            event,
            "transcript_refs",
            "transcriptRefs",
            "transcript_entry_ids",
            "transcriptEntryIds",
        ),
        _event_field_value(
            event,
            "transcript_ref",
            "transcriptRef",
            "transcript_entry_id",
            "transcriptEntryId",
            "transcript_id",
            "transcriptId",
        ),
        _event_metadata_value(
            event,
            "transcript_refs",
            "transcriptRefs",
            "transcript_entry_ids",
            "transcriptEntryIds",
        ),
        _event_metadata_value(
            event,
            "transcript_ref",
            "transcriptRef",
            "transcript_entry_id",
            "transcriptEntryId",
            "transcript_id",
            "transcriptId",
        ),
    )
    for candidate in candidate_values:
        values.extend(_coerce_string_refs(candidate))
    return tuple(values)


def _event_field_value(event: object, *keys: str) -> object:
    if isinstance(event, Mapping):
        for key in keys:
            if key in event:
                return event[key]
        return None
    for key in keys:
        value = getattr(event, key, None)
        if value is not None:
            return value
    return None


def _coerce_string_refs(value: object) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str) and item.strip())
    return ()


def _compare_transcript_projection(
    bundle: Gate3ARecordedBundle,
    runner_events: Iterable[object],
    *,
    event_projection: Gate3AParityStatus,
) -> Gate3AParityStatus:
    if event_projection != "pass":
        return "mismatch"
    recorded = _record_transcript_ids(bundle)
    actual = tuple(
        transcript_ref
        for event in runner_events
        for transcript_ref in _event_transcript_refs(event)
    )
    if not recorded or not actual:
        return "not_applicable"
    if recorded == actual:
        return "pass"
    return "mismatch"


async def _compare_recorded_tool_projection(
    bundle: Gate3ARecordedBundle,
    runner_events: Iterable[object],
) -> Gate3AParityStatus:
    recorded = {tool.tool_call_id for tool in bundle.recorded_tool_results}
    actual = {tool_call_id for event in runner_events if (tool_call_id := _tool_call_id(event))}
    for tool_call_id in actual:
        _resolve_recorded_tool_output(tool_call_id=tool_call_id, bundle=bundle)
    if not recorded and not actual:
        return "not_applicable"
    if recorded == actual:
        return "pass"
    if actual - recorded:
        return "extra"
    if recorded - actual:
        return "missing"
    return "mismatch"


def _resolve_recorded_tool_output(
    *,
    tool_call_id: str,
    bundle: Gate3ARecordedBundle,
) -> Mapping[str, object] | None:
    for tool_result in bundle.recorded_tool_results:
        if tool_result.tool_call_id == tool_call_id:
            return tool_result.output_metadata
    return None


def _failure_report(
    bundle: Gate3ARecordedBundle,
    status: Gate3AParityStatus,
    failure: str,
) -> Gate3AComparisonReport:
    return build_gate3a_comparison_report(
        bundle_id=bundle.bundle_id,
        shadow_run_id=f"shadow_local_{uuid4().hex[:12]}",
        recipe_snapshot_id=bundle.recipe.recipe_snapshot_id,
        event_projection=status,
        transcript_projection=status,
        sse_projection="not_applicable",
        failures=(failure[:180],),
    )


def _require_local_adk_runner(local_runner: Gate3ALocalRunner) -> None:
    if type(local_runner) is not Gate3ALocalReplayRunner:
        raise ValueError("Gate 3A local runner must use the first-party replay adapter")
    if tuple(getattr(local_runner, "adk_primitives", ())) != _LOCAL_ADK_PRIMITIVES:
        raise ValueError("Gate 3A local runner must declare official ADK primitives")
    if getattr(local_runner, "local_only", None) is not True:
        raise ValueError("Gate 3A local runner must be marked local_only")
    for flag_name in _RUNNER_ATTACHMENT_FLAGS:
        if getattr(local_runner, flag_name, None) is not False:
            raise ValueError("Gate 3A local runner attachment flags must be false")


def _require_recorded_output_tool_policy(
    tool_policy: RecordedOutputToolPolicy | None,
) -> None:
    if tool_policy is not None and type(tool_policy) is not RecordedOutputToolPolicy:
        raise ValueError("Gate 3A recorded output policy must be first-party metadata only")


def validate_gate3a_report_output_dir(path: str | Path | None) -> Path | None:
    candidate = validate_gate3a_local_path(path)
    if candidate is None:
        return None
    resolved_candidate = candidate.resolve(strict=False)
    if not _is_isolated_gate3a_output_dir(resolved_candidate):
        raise ValueError("Gate 3A report output directory must be temp or explicitly isolated")
    return resolved_candidate


def _is_isolated_gate3a_output_dir(path: Path) -> bool:
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if path == temp_root or path.is_relative_to(temp_root):
        return True
    return any(
        part in {".gate3a-isolated", "gate3a-output", "gate3a-reports"}
        for part in path.parts
    )


def _write_gate3a_report_artifact(
    report: Gate3AComparisonReport,
    output_dir: Path | None,
) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report.shadow_run_id}.comparison.json"
    output_payload = report.model_dump(by_alias=True, mode="json", warnings=False)
    output_path.write_text(
        json.dumps(output_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "Gate3ALocalRunner",
    "Gate3ALocalReplayRunner",
    "Gate3AReplayError",
    "RecordedOutputToolPolicy",
    "run_gate3a_recorded_replay",
    "run_gate3a_recorded_replay_async",
    "validate_gate3a_report_output_dir",
    "validate_gate3a_local_path",
]
