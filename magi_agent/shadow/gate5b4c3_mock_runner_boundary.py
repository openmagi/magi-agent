from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationAuthorityFlags,
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationDiagnostic,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)


MockRunner = Callable[[Gate5B4C3ShadowGenerationRequest], object]
Gate5B4C3MockRunnerFailOpenReason = Literal[
    "none",
    "not_accepted",
    "mock_runner_missing",
    "mock_runner_error",
    "mock_runner_timeout",
]
Gate5B4C3MockOutputRejectionReason = Literal[
    "none",
    "unsafe_mock_output",
    "mock_output_too_large",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_MAX_OUTPUT_BYTES = 16_384
_DEFAULT_PREVIEW_BYTES = 512
_UNSAFE_OUTPUT_FIELD_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "endpointurl",
        "outputpath",
        "callerprovidedoutputpath",
        "messages",
        "rawusertext",
        "fulltranscript",
        "privatememory",
        "memoryrecall",
        "rawtoolargs",
        "rawtoolresult",
        "rawtooloutput",
        "workspacepath",
        "k8spath",
        "deploypath",
        "kubeconfig",
        "telegramtoken",
        "childprompt",
        "childoutput",
        "evidenceblockmode",
        "productionwritedirective",
        "runtimeselectordirective",
        "uservisibleresponseauthority",
        "hiddenreasoning",
        "chainofthought",
        "privatereasoning",
        "reasoningtrace",
    }
)
_UNSAFE_OUTPUT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"(?:Cookie|Set-Cookie):\s*[^;\r\n]+(?:;[^\r\n]*)?|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|private[_-]?key|session[_-]?key)[\"']?\s*:"
    r"\s*[\"'][^\"'\r\n]{4,}[\"']|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+|"
    r"hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\b(?:kubectl|helm|kustomize|sealed-secrets|kubeconfig)\b|"
    r"\bmagi\.pro\b\S*|"
    r"https?://\S+|"
    r"s3://\S+"
    r")",
    re.IGNORECASE,
)


class Gate5B4C3MockRunnerBoundaryResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate5b4c3.mockRunnerBoundary.v1"] = Field(
        default="gate5b4c3.mockRunnerBoundary.v1",
        alias="schemaVersion",
    )
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic
    response_authority: Literal["typescript"] = Field(
        default="typescript",
        alias="responseAuthority",
    )
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    mock_runner_invoked: bool = Field(default=False, alias="mockRunnerInvoked")
    mock_runner_completed: bool = Field(default=False, alias="mockRunnerCompleted")
    mock_runner_failed_open: bool = Field(default=False, alias="mockRunnerFailedOpen")
    fail_open_reason: Gate5B4C3MockRunnerFailOpenReason = Field(
        default="none",
        alias="failOpenReason",
    )
    mock_output_accepted: bool = Field(default=False, alias="mockOutputAccepted")
    mock_output_rejection_reason: Gate5B4C3MockOutputRejectionReason = Field(
        default="none",
        alias="mockOutputRejectionReason",
    )
    mock_output_digest: str | None = Field(default=None, alias="mockOutputDigest")
    mock_output_preview_internal: str | None = Field(
        default=None,
        alias="mockOutputPreviewInternal",
    )
    user_visible_output: str | None = Field(default=None, alias="userVisibleOutput")
    authority: Gate5B4C3ShadowGenerationAuthorityFlags = Field(
        default_factory=Gate5B4C3ShadowGenerationAuthorityFlags,
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> "Gate5B4C3MockRunnerBoundaryResult":
        data = {
            key: value.model_dump(by_alias=True, mode="python", warnings=False)
            if isinstance(value, BaseModel)
            else value
            for key, value in values.items()
        }
        return cls(**data)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> "Gate5B4C3MockRunnerBoundaryResult":
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            name_to_alias = {
                name: field.alias or name
                for name, field in self.__class__.model_fields.items()
            }
            data.update({name_to_alias.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _force_non_user_visible(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "typescript"
        data["diagnosticOnly"] = True
        data["localOnly"] = True
        data["userVisibleOutput"] = None
        return data

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return Gate5B4C3ShadowGenerationAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def run_gate5b4c3_mock_runner_boundary(
    request: Gate5B4C3ShadowGenerationRequest,
    *,
    config: Gate5B4C3ShadowGenerationConfig | None = None,
    mock_runner: MockRunner | None = None,
    preview_byte_limit: int = _DEFAULT_PREVIEW_BYTES,
) -> Gate5B4C3MockRunnerBoundaryResult:
    started = time.monotonic()
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(request, config=config)
    if not diagnostic.accepted:
        return _result(
            diagnostic,
            fail_open_reason="not_accepted",
            latency_started=started,
        )

    if mock_runner is None:
        return _result(
            diagnostic,
            mock_runner_failed_open=True,
            fail_open_reason="mock_runner_missing",
            latency_started=started,
        )

    try:
        raw_output = mock_runner(request)
    except TimeoutError:
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_failed_open=True,
            fail_open_reason="mock_runner_timeout",
            latency_started=started,
        )
    except Exception:
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_failed_open=True,
            fail_open_reason="mock_runner_error",
            latency_started=started,
        )

    if _elapsed_ms(started) > request.budgets.python_runner_timeout_ms:
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_completed=False,
            mock_runner_failed_open=True,
            fail_open_reason="mock_runner_timeout",
            latency_started=started,
        )

    output_text = _stringify_mock_output(raw_output)
    output_digest = _sha256_digest(output_text)
    if not _is_previewable_mock_output(raw_output):
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_completed=True,
            mock_output_rejection_reason="unsafe_mock_output",
            mock_output_digest=output_digest,
            latency_started=started,
        )
    if _contains_unsafe_output(raw_output):
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_completed=True,
            mock_output_rejection_reason="unsafe_mock_output",
            mock_output_digest=output_digest,
            latency_started=started,
        )
    if len(output_text.encode("utf-8")) > min(
        _MAX_OUTPUT_BYTES,
        request.budgets.max_diagnostic_artifact_bytes,
    ):
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_completed=True,
            mock_output_rejection_reason="mock_output_too_large",
            mock_output_digest=output_digest,
            latency_started=started,
        )
    if _UNSAFE_OUTPUT_RE.search(output_text):
        return _result(
            diagnostic,
            mock_runner_invoked=True,
            mock_runner_completed=True,
            mock_output_rejection_reason="unsafe_mock_output",
            mock_output_digest=output_digest,
            latency_started=started,
        )

    return _result(
        diagnostic,
        mock_runner_invoked=True,
        mock_runner_completed=True,
        mock_output_accepted=True,
        mock_output_digest=output_digest,
        mock_output_preview_internal=_cap_utf8(
            output_text,
            max(0, min(preview_byte_limit, request.budgets.max_diagnostic_output_preview_bytes)),
        ),
        latency_started=started,
    )


def _result(
    diagnostic: Gate5B4C3ShadowGenerationDiagnostic,
    *,
    mock_runner_invoked: bool = False,
    mock_runner_completed: bool = False,
    mock_runner_failed_open: bool = False,
    fail_open_reason: Gate5B4C3MockRunnerFailOpenReason = "none",
    mock_output_accepted: bool = False,
    mock_output_rejection_reason: Gate5B4C3MockOutputRejectionReason = "none",
    mock_output_digest: str | None = None,
    mock_output_preview_internal: str | None = None,
    latency_started: float,
) -> Gate5B4C3MockRunnerBoundaryResult:
    updated_diagnostic = diagnostic.model_copy(
        update={"latencyMs": max(diagnostic.latency_ms, _elapsed_ms(latency_started))}
    )
    return Gate5B4C3MockRunnerBoundaryResult(
        diagnostic=updated_diagnostic.model_dump(by_alias=True, mode="python"),
        mockRunnerInvoked=mock_runner_invoked,
        mockRunnerCompleted=mock_runner_completed,
        mockRunnerFailedOpen=mock_runner_failed_open,
        failOpenReason=fail_open_reason,
        mockOutputAccepted=mock_output_accepted,
        mockOutputRejectionReason=mock_output_rejection_reason,
        mockOutputDigest=mock_output_digest,
        mockOutputPreviewInternal=mock_output_preview_internal,
    )


def _stringify_mock_output(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _is_previewable_mock_output(value: object) -> bool:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _is_previewable_mock_output(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return all(_is_previewable_mock_output(child) for child in value)
    return False


def _sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contains_unsafe_output(value: object) -> bool:
    if isinstance(value, str):
        return bool(_UNSAFE_OUTPUT_RE.search(value))
    if is_dataclass(value) and not isinstance(value, type):
        return _contains_unsafe_output(asdict(value))
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _unsafe_output_key(key) or _contains_unsafe_output(child):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_unsafe_output(child) for child in value)
    if hasattr(value, "__dict__"):
        return _contains_unsafe_output(vars(value))
    return False


def _unsafe_output_key(value: object) -> bool:
    if not isinstance(value, str):
        return _contains_unsafe_output(value)
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return normalized in _UNSAFE_OUTPUT_FIELD_NAMES or bool(_UNSAFE_OUTPUT_RE.search(value))


def _cap_utf8(value: str, byte_limit: int) -> str:
    if byte_limit <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = [
    "Gate5B4C3MockOutputRejectionReason",
    "Gate5B4C3MockRunnerBoundaryResult",
    "Gate5B4C3MockRunnerFailOpenReason",
    "MockRunner",
    "run_gate5b4c3_mock_runner_boundary",
]
