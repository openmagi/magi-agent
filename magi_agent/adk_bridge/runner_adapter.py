from __future__ import annotations

from collections.abc import AsyncIterator
import math
import os
import re

from google.genai import types
from pydantic_core import PydanticSerializationError
from pydantic import BaseModel, ConfigDict, Field, field_validator


_STREAMING_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def _adk_streaming_enabled() -> bool:
    # I-4: routed through the typed flag registry. The deny-set
    # ``_STREAMING_DISABLED_VALUES`` is wider than ``flag_bool``'s
    # strict-truthy set, so the helper reads ``flag_str`` and applies
    # the same deny check (default-ON when unset).
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    raw = (flag_str("MAGI_ADK_STREAMING") or "").strip().lower()
    return raw not in _STREAMING_DISABLED_VALUES


ADK_RUNNER_KWARG_ALLOWLIST = frozenset(
    {"user_id", "session_id", "invocation_id", "new_message"}
)
FORBIDDEN_OPENMAGI_MESSAGE_KEYS = frozenset(
    {
        "state_delta",
        "stateDelta",
        "openmagi.state_delta",
        "openmagi.stateDelta",
        "run_config",
        "runConfig",
        "openmagi.run_config",
        "openmagi.runConfig",
        "traffic_attached",
        "trafficAttached",
        "openmagi.traffic_attached",
        "openmagi.trafficAttached",
        "execution_attached",
        "executionAttached",
        "openmagi.execution_attached",
        "openmagi.executionAttached",
        "openmagi.harness",
        "harness_state",
        "harnessState",
        "openmagi.harness_state",
        "openmagi.harnessState",
        "openmagi.evidence",
        "evidence_contracts",
        "evidenceContracts",
        "openmagi.evidence_contracts",
        "openmagi.evidenceContracts",
        "control",
        "openmagi.control",
        "openmagi.state",
        "current_turn_id",
        "currentTurnId",
        "openmagi.current_turn_id",
        "openmagi.currentTurnId",
    }
)
FORBIDDEN_OPENMAGI_CONTAINER_KEYS = frozenset(
    {
        "state",
        "harness",
        "harness_state",
        "harnessState",
        "evidence",
        "evidence_contracts",
        "evidenceContracts",
        "control",
        "run_config",
        "runConfig",
        "state_delta",
        "stateDelta",
        "traffic_attached",
        "trafficAttached",
        "execution_attached",
        "executionAttached",
        "current_turn_id",
        "currentTurnId",
    }
)
JSON_COMPATIBLE_ADK_CONTENT_ERROR = (
    "new_message must contain only JSON-compatible ADK Content values"
)


def _normalize_openmagi_key(value: str) -> str:
    camel_spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    normalized = re.sub(r"[^a-z0-9]+", "_", camel_spaced.lower()).strip("_")
    if normalized == "open_magi":
        return "openmagi"
    if normalized.startswith("open_magi_"):
        return f"openmagi_{normalized.removeprefix('open_magi_')}"
    return normalized


FORBIDDEN_OPENMAGI_MESSAGE_KEYS_NORMALIZED = frozenset(
    _normalize_openmagi_key(key) for key in FORBIDDEN_OPENMAGI_MESSAGE_KEYS
) | {"openmagi"}
FORBIDDEN_OPENMAGI_CONTAINER_KEYS_NORMALIZED = frozenset(
    _normalize_openmagi_key(key) for key in FORBIDDEN_OPENMAGI_CONTAINER_KEYS
)


def _is_openmagi_namespace_key(normalized_key: str) -> bool:
    return (
        normalized_key == "openmagi"
        or normalized_key.startswith("openmagi_")
        or normalized_key.startswith("openmagi")
    )


def _find_forbidden_openmagi_container_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                continue
            normalized_key = _normalize_openmagi_key(key)
            if normalized_key == "openmagi":
                found = _find_forbidden_openmagi_container_key(nested_value)
                if found is not None:
                    return found
                return key
            if normalized_key in FORBIDDEN_OPENMAGI_CONTAINER_KEYS_NORMALIZED:
                return f"openmagi.{key}"
            found = _find_forbidden_openmagi_container_key(nested_value)
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_forbidden_openmagi_container_key(item)
            if found is not None:
                return found
    return None


def _find_forbidden_openmagi_message_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                continue
            normalized_key = _normalize_openmagi_key(key)
            if (
                normalized_key in FORBIDDEN_OPENMAGI_MESSAGE_KEYS_NORMALIZED
                or _is_openmagi_namespace_key(normalized_key)
            ):
                if normalized_key == "openmagi":
                    found = _find_forbidden_openmagi_container_key(nested_value)
                    if found is not None:
                        return found
                return str(key)
            found = _find_forbidden_openmagi_message_key(nested_value)
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_forbidden_openmagi_message_key(item)
            if found is not None:
                return found
    return None


def _find_forbidden_openmagi_identifier_value(
    value: object,
    *,
    inspect_strings: bool = False,
) -> str | None:
    if isinstance(value, str):
        if not inspect_strings:
            return None
        for candidate in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
            normalized_key = _normalize_openmagi_key(candidate)
            if (
                normalized_key in FORBIDDEN_OPENMAGI_MESSAGE_KEYS_NORMALIZED
                or _is_openmagi_namespace_key(normalized_key)
            ):
                return candidate
        return None
    if isinstance(value, dict):
        for key, nested_value in value.items():
            found = _find_forbidden_openmagi_identifier_value(
                nested_value,
                inspect_strings=inspect_strings or key in {"args", "response"},
            )
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _find_forbidden_openmagi_identifier_value(
                item,
                inspect_strings=inspect_strings,
            )
            if found is not None:
                return found
    return None


def _validate_json_like_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError(JSON_COMPATIBLE_ADK_CONTENT_ERROR)
    if isinstance(value, list):
        for item in value:
            _validate_json_like_value(item)
        return
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError(JSON_COMPATIBLE_ADK_CONTENT_ERROR)
            _validate_json_like_value(nested_value)
        return
    raise ValueError(JSON_COMPATIBLE_ADK_CONTENT_ERROR)


def _copy_validated_adk_content(value: types.Content) -> types.Content:
    if type(value) is not types.Content:
        raise ValueError("new_message must be google.genai.types.Content")

    # Validate JSON-like compatibility per-part so that image parts (which carry
    # binary ``inline_data.data`` bytes -- a valid ADK blob type) are exempted
    # while non-image parts (text, function_call, function_response) are still
    # checked for JSON-like safety.  The function_call.args check is the
    # primary guard: tool input values must be JSON-native so they can be
    # passed to tools as JSON; bytes in args are rejected here even though
    # Pydantic would silently base64-encode them under ``mode="json"``.
    for part in value.parts or ():
        if getattr(part, "inline_data", None) is not None:
            # Image part -- ``inline_data.data`` is binary (bytes); this is a
            # legitimate ADK content type.  Skip JSON-like check; the mode=json
            # dump below handles serialisation correctly via base64 encoding.
            continue
        _validate_json_like_value(part.model_dump(by_alias=True, exclude_none=True))

    try:
        dumped = value.model_dump(by_alias=True, exclude_none=True, mode="json")
    except PydanticSerializationError as exc:
        raise ValueError(JSON_COMPATIBLE_ADK_CONTENT_ERROR) from exc

    content = types.Content.model_validate(dumped)
    forbidden_key = _find_forbidden_openmagi_message_key(
        content.model_dump(by_alias=True, exclude_none=True)
    )
    if forbidden_key is not None:
        raise ValueError(
            f"new_message contains forbidden OpenMagi key: {forbidden_key}"
        )
    forbidden_identifier = _find_forbidden_openmagi_identifier_value(
        content.model_dump(by_alias=True, exclude_none=True)
    )
    if forbidden_identifier is not None:
        raise ValueError(
            "new_message contains forbidden OpenMagi identifier: "
            f"{forbidden_identifier}"
        )
    return content


class RunnerTurnInput(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    user_id: str = Field(alias="userId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    invocation_id: str = Field(alias="invocationId")
    new_message: types.Content = Field(alias="newMessage")
    harness_state: object = Field(alias="harnessState")
    state_delta: dict[str, object] = Field(default_factory=dict, alias="stateDelta")
    run_config: object | None = Field(default=None, alias="runConfig")

    @field_validator("new_message", mode="before")
    @classmethod
    def _require_adk_content(cls, value: object) -> object:
        if type(value) is not types.Content:
            raise ValueError("new_message must be google.genai.types.Content")
        return value

    @field_validator("new_message")
    @classmethod
    def _reject_openmagi_message_state(cls, value: types.Content) -> types.Content:
        return _copy_validated_adk_content(value)


class OpenMagiRunnerAdapter:
    def __init__(self, *, runner: object, num_recent_events: int | None = None) -> None:
        self.runner = runner
        # Driver-owned knob: how many durable-session events to load per turn.
        # ``None`` (default) -> no bound, RunConfig carries no GetSessionConfig
        # (byte-identical to the pre-B5 path). Set by ``MagiEngineDriver`` when
        # the durable session substrate is active; never accepted from external
        # run_config (the anti-side-channel check at _build_adk_runner_kwargs
        # remains intact for all caller-supplied run_config fields).
        self._num_recent_events: int | None = num_recent_events

    def _adapter_run_config(self) -> "object | None":
        # Lazy import keeps ADK off the cold-start critical path.
        from google.adk.agents.run_config import RunConfig, StreamingMode  # noqa: PLC0415

        kwargs: dict[str, object] = {"streaming_mode": StreamingMode.SSE}
        if self._num_recent_events is not None:
            # B5: thread the driver-owned bound through GetSessionConfig so the
            # ADK session service fetches at most N events per turn for a durable
            # session. GetSessionConfig is imported lazily (same fail-open
            # pattern as the legacy gate5b4c3 _run_config helper).
            try:
                from google.adk.sessions.base_session_service import (  # noqa: PLC0415
                    GetSessionConfig,
                )

                kwargs["get_session_config"] = GetSessionConfig(
                    num_recent_events=self._num_recent_events
                )
            except Exception:  # noqa: BLE001
                pass
        return RunConfig(**kwargs)

    def _build_adk_runner_kwargs(self, turn_input: RunnerTurnInput) -> dict[str, object]:
        # Caller-provided run_config/state_delta remain blocked — they must not
        # become an ADK side channel. The adapter injects its OWN streaming
        # RunConfig below, after the allowlist filter, so it cannot be spoofed.
        kwargs: dict[str, object] = {
            "user_id": turn_input.user_id,
            "session_id": turn_input.session_id,
            "invocation_id": turn_input.invocation_id,
            "new_message": _copy_validated_adk_content(turn_input.new_message),
        }
        filtered = {
            key: value
            for key, value in kwargs.items()
            if key in ADK_RUNNER_KWARG_ALLOWLIST
        }
        if _adk_streaming_enabled():
            run_config = self._adapter_run_config()
            if run_config is not None:
                filtered["run_config"] = run_config
        return filtered

    async def run_turn(self, turn_input: RunnerTurnInput) -> AsyncIterator[object]:
        # The resolved harness snapshot stays on turn_input for OpenMagi policy
        # boundaries; it is not copied to adapter state or ADK Runner kwargs.
        # Caller-provided state_delta/run_config are held back so OpenMagi-only
        # state cannot become an ADK side channel. The adapter injects its own
        # streaming RunConfig(streaming_mode=SSE) so token deltas flow correctly.
        async for event in self.runner.run_async(
            **self._build_adk_runner_kwargs(turn_input)
        ):
            yield event

    async def collect_events(self, turn_input: RunnerTurnInput) -> list[object]:
        return [event async for event in self.run_turn(turn_input)]
