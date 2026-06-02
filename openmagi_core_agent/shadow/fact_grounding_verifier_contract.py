from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


FactGroundingMode: TypeAlias = Literal["A", "B"]
FactGroundingVerdict: TypeAlias = Literal["GROUNDED", "DISTORTED", "FABRICATED"]
FactGroundingConfidence: TypeAlias = Literal["high", "low"]
FactGroundingFutureAdkTarget: TypeAlias = Literal[
    "ValidatorSet_or_AgentEvaluator_metadata",
]
FactGroundingCategory: TypeAlias = Literal[
    "grounded_json_tool_values_match",
    "distorted_identifier_mismatch",
    "grounded_no_tool_results",
    "grounded_general_knowledge_despite_tool_output",
    "grounded_numbers_within_tolerance",
    "distorted_significant_number_mismatch",
    "low_confidence_grounded_values_not_referenced",
    "fabricated_explicit_file_config_claim_without_read_tool",
    "grounded_general_knowledge_no_tools",
    "fabricated_english_config_claim_without_read_tool",
    "fabricated_korean_script_reference_without_read_tool",
    "grounded_honest_uncertainty",
    "low_confidence_specific_details_without_file_claim_pattern",
]
FactGroundingReasonCode: TypeAlias = Literal[
    "all_tool_values_verified",
    "identifier_mismatch",
    "no_tool_results",
    "numbers_within_tolerance",
    "significant_number_mismatch",
    "values_not_referenced_needs_llm",
    "explicit_file_config_claim_without_read_tool",
    "honest_uncertainty",
    "specific_details_without_file_claim_needs_llm",
    "general_knowledge_or_no_specific_claims",
    "no_extractable_values",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_REQUIRED_CATEGORIES = set(FactGroundingCategory.__args__)  # type: ignore[attr-defined]
_REQUIRED_CASE_ORDER = (
    "mode_a_grounded_json_tool_values_match",
    "mode_a_distorted_identifier_mismatch",
    "mode_a_grounded_no_tool_results",
    "mode_a_grounded_general_knowledge_despite_tool_output",
    "mode_a_grounded_numbers_within_tolerance",
    "mode_a_distorted_significant_number_mismatch",
    "mode_a_low_confidence_grounded_values_not_referenced",
    "mode_b_fabricated_explicit_file_config_claim_without_read_tool",
    "mode_b_grounded_general_knowledge_no_tools",
    "mode_b_fabricated_english_config_claim_without_read_tool",
    "mode_b_fabricated_korean_script_reference_without_read_tool",
    "mode_b_grounded_honest_uncertainty",
    "mode_b_low_confidence_specific_details_without_file_claim_pattern",
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_RAW_TOKENS = (
    "Bearer unsafe",
    "ghp_factsecret",
    "sk-fact-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
)
_FORBIDDEN_PUBLIC_TOKENS = _FORBIDDEN_RAW_TOKENS + (
    "config.json에 따르면",
    "gemini-2.5-pro",
    "GPT-4o",
    "temperature",
    "파일을 읽어보니",
    "총 1500개의",
    "hidden reasoning",
    "rawOutput",
)
_FORBIDDEN_TRUE_KEYS = frozenset(
    {
        "adk_eval_attached",
        "adk_runner_invoked",
        "agent_evaluator_attached",
        "block_mode_enabled",
        "browser_executed",
        "browser_search_called",
        "eval_attached",
        "evaluation_attached",
        "hook_attached",
        "llm_judge_called",
        "live_llm_judge",
        "live_tool_dispatched",
        "memory_provider_called",
        "model_called",
        "prompt_mutated",
        "provider_called",
        "runner_invoked",
        "search_executed",
        "source_fetched",
        "tool_host_dispatched",
        "toolhost_dispatched",
        "transcript_read",
        "user_visible_output",
        "web_search_executed",
    }
)
_MODE_BY_CATEGORY: dict[FactGroundingCategory, FactGroundingMode] = {
    "grounded_json_tool_values_match": "A",
    "distorted_identifier_mismatch": "A",
    "grounded_no_tool_results": "A",
    "grounded_general_knowledge_despite_tool_output": "A",
    "grounded_numbers_within_tolerance": "A",
    "distorted_significant_number_mismatch": "A",
    "low_confidence_grounded_values_not_referenced": "A",
    "fabricated_explicit_file_config_claim_without_read_tool": "B",
    "grounded_general_knowledge_no_tools": "B",
    "fabricated_english_config_claim_without_read_tool": "B",
    "fabricated_korean_script_reference_without_read_tool": "B",
    "grounded_honest_uncertainty": "B",
    "low_confidence_specific_details_without_file_claim_pattern": "B",
}
_UNGROUNDED_FILE_CLAIM_PATTERNS = (
    re.compile(r"(?:파일을?\s*(?:읽어|확인해|열어)\s*보니)"),
    re.compile(r"(?:파일에\s*따르면)"),
    re.compile(r"(?:스크립트에\s*따르면)"),
    re.compile(r"(?:설정\s*(?:파일|에)\s*(?:따르면|보면|에서|에는))"),
    re.compile(r"(?:config\s+(?:uses?|says?|shows?|contains?|specifies?))\b", re.I),
    re.compile(
        r"(?:the\s+(?:file|config|script|document)\s+"
        r"(?:says?|shows?|contains?|specifies?|uses?))\b",
        re.I,
    ),
    re.compile(r"(?:according\s+to\s+(?:the\s+)?(?:file|config|script|document))\b", re.I),
    re.compile(r"(?:I\s+(?:read|checked|looked at)\s+(?:the\s+)?(?:file|config))", re.I),
)
_HONEST_UNCERTAINTY_PATTERNS = (
    re.compile(r"(?:확인해\s*봐야|읽어\s*보겠|확인\s*하겠|모르겠)"),
    re.compile(
        r"(?:let\s+me\s+check|I(?:'m|\s+am)\s+not\s+sure|"
        r"I\s+need\s+to\s+(?:read|check))",
        re.I,
    ),
)


class FactGroundingVerifierAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    llm_judge_called: Literal[False] = Field(default=False, alias="llmJudgeCalled")
    prompt_mutated: Literal[False] = Field(default=False, alias="promptMutated")
    hook_attached: Literal[False] = Field(default=False, alias="hookAttached")
    block_mode_enabled: Literal[False] = Field(default=False, alias="blockModeEnabled")
    adk_eval_attached: Literal[False] = Field(default=False, alias="adkEvalAttached")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    transcript_read: Literal[False] = Field(default=False, alias="transcriptRead")
    source_fetched: Literal[False] = Field(default=False, alias="sourceFetched")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    web_search_executed: Literal[False] = Field(default=False, alias="webSearchExecuted")
    provider_called: Literal[False] = Field(default=False, alias="providerCalled")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "llm_judge_called",
        "prompt_mutated",
        "hook_attached",
        "block_mode_enabled",
        "adk_eval_attached",
        "adk_runner_invoked",
        "toolhost_dispatched",
        "transcript_read",
        "source_fetched",
        "browser_executed",
        "web_search_executed",
        "provider_called",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class FactGroundingToolOutput(BaseModel):
    model_config = _MODEL_CONFIG

    tool_name: str = Field(alias="toolName")
    raw_output: str = Field(alias="rawOutput")
    digest: str


class FactGroundingVerifierCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    mode: FactGroundingMode
    category: FactGroundingCategory
    assistant_text: str = Field(alias="assistantText")
    assistant_text_digest: str = Field(alias="assistantTextDigest")
    tool_outputs: tuple[FactGroundingToolOutput, ...] = Field(alias="toolOutputs")
    expected_verdict: FactGroundingVerdict = Field(alias="expectedVerdict")
    expected_confidence: FactGroundingConfidence = Field(alias="expectedConfidence")
    expected_reason_code: FactGroundingReasonCode = Field(alias="expectedReasonCode")
    deterministic_only: Literal[True] = Field(alias="deterministicOnly")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    future_adk_target: FactGroundingFutureAdkTarget = Field(alias="futureAdkTarget")
    metadata: dict[str, object]
    attachment_flags: FactGroundingVerifierAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.case_id.strip():
            raise ValueError("fact grounding case requires caseId")
        if not self.assistant_text.strip():
            raise ValueError("fact grounding case requires assistantText")
        if self.mode != _MODE_BY_CATEGORY[self.category]:
            raise ValueError("fact grounding mode must match category")
        if self.mode == "B" and self.tool_outputs:
            raise ValueError("Mode B fact grounding cases must not include tool outputs")
        verdict = deterministic_fact_grounding_verdict(
            self.mode,
            self.assistant_text,
            self.tool_outputs,
        )
        if verdict["verdict"] != self.expected_verdict:
            raise ValueError("fact grounding expectedVerdict does not match deterministic verdict")
        if verdict["confidence"] != self.expected_confidence:
            raise ValueError(
                "fact grounding expectedConfidence does not match deterministic verdict",
            )
        if verdict["reasonCode"] != self.expected_reason_code:
            raise ValueError(
                "fact grounding expectedReasonCode does not match deterministic verdict",
            )
        return self


class FactGroundingVerifierFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["factGroundingVerifierFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    target_runtime: Literal["python-adk-future"] = Field(alias="targetRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    future_adk_target: FactGroundingFutureAdkTarget = Field(alias="futureAdkTarget")
    attachment_flags: FactGroundingVerifierAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[FactGroundingVerifierCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("fact grounding caseIds must be unique")
        if tuple(case_ids) != _REQUIRED_CASE_ORDER:
            raise ValueError("fact grounding fixture case order must match contract")
        categories = {case.category for case in self.cases}
        if categories != _REQUIRED_CATEGORIES:
            raise ValueError("fact grounding fixture must cover all deterministic categories")
        for case in self.cases:
            if case.future_adk_target != self.future_adk_target:
                raise ValueError("case futureAdkTarget must match fixture")
        return self


class FactGroundingVerifierProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    future_adk_target: FactGroundingFutureAdkTarget = Field(alias="futureAdkTarget")
    attachment_flags: FactGroundingVerifierAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_mode: dict[str, int] = Field(alias="byMode")
    by_verdict: dict[str, int] = Field(alias="byVerdict")
    by_confidence: dict[str, int] = Field(alias="byConfidence")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def deterministic_fact_grounding_verdict(
    mode: FactGroundingMode,
    assistant_text: str,
    tool_outputs: tuple[FactGroundingToolOutput, ...] = (),
) -> dict[str, str]:
    if mode == "A":
        return _ground_against_tool_outputs(tool_outputs, assistant_text)
    return _detect_ungrounded_file_claims(assistant_text)


def load_fact_grounding_verifier_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> FactGroundingVerifierFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return FactGroundingVerifierFixture.model_validate(payload)


def project_fact_grounding_verifier_fixture(
    fixture: FactGroundingVerifierFixture | Mapping[str, Any],
) -> FactGroundingVerifierProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    mode_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    case_snapshots: dict[str, dict[str, object]] = {}

    for case in safe_fixture.cases:
        mode_counts[case.mode] += 1
        verdict_counts[case.expected_verdict] += 1
        confidence_counts[case.expected_confidence] += 1
        snapshot = _case_snapshot(case)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot

    return FactGroundingVerifierProjection(
        fixtureId=safe_fixture.fixture_id,
        localDiagnostic=True,
        metadataOnly=True,
        defaultOff=True,
        futureAdkTarget=safe_fixture.future_adk_target,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byMode=dict(mode_counts),
        byVerdict=dict(verdict_counts),
        byConfidence=dict(confidence_counts),
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: FactGroundingVerifierFixture | Mapping[str, Any],
) -> FactGroundingVerifierFixture:
    if isinstance(fixture, FactGroundingVerifierFixture):
        return FactGroundingVerifierFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False),
        )
    return FactGroundingVerifierFixture.model_validate(fixture)


def _case_snapshot(case: FactGroundingVerifierCase) -> dict[str, object]:
    return {
        "caseId": case.case_id,
        "mode": case.mode,
        "category": case.category,
        "verdict": case.expected_verdict,
        "confidence": case.expected_confidence,
        "reasonCode": case.expected_reason_code,
        "deterministicOnly": True,
        "metadataOnly": True,
        "defaultOff": True,
        "futureAdkTarget": case.future_adk_target,
        "toolResultCount": len(case.tool_outputs),
        "assistantTextDigest": case.assistant_text_digest,
        "toolOutputDigests": tuple(output.digest for output in case.tool_outputs),
    }


def _ground_against_tool_outputs(
    tool_outputs: tuple[FactGroundingToolOutput, ...],
    assistant_text: str,
) -> dict[str, str]:
    if not tool_outputs:
        return _verdict("GROUNDED", "high", "no_tool_results")

    extracted = _extract_tool_output_values(tool_outputs)
    if not extracted:
        return _verdict("GROUNDED", "high", "no_extractable_values")

    for value in extracted:
        if value["kind"] != "string" or len(value["value"]) < 3:
            continue
        if not re.fullmatch(r"[a-z][\w.-]*", value["value"], re.I):
            continue
        if "-" not in value["value"]:
            continue
        if value["value"].lower() in assistant_text.lower():
            continue
        identifiers = re.findall(r"\b[a-zA-Z][\w.-]*-[\w.-]+\b", assistant_text)
        if identifiers:
            return _verdict("DISTORTED", "high", "identifier_mismatch")

    tool_numbers: set[float] = set()
    tool_strings: dict[str, str] = {}
    for value in extracted:
        if value["kind"] == "number":
            number = _parse_number(value["value"])
            if number is not None and number > 1:
                tool_numbers.add(number)
        elif len(value["value"]) >= 3:
            tool_strings[value["key"]] = value["value"]

    assistant_numbers = [
        number
        for number in (
            _parse_number(match)
            for match in re.findall(r"(?<!\d)\d+(?:\.\d+)?(?!\d)", assistant_text)
        )
        if number is not None and number > 1
    ]

    for assistant_number in assistant_numbers:
        for tool_number in tool_numbers:
            ratio = abs(assistant_number - tool_number) / abs(tool_number)
            if ratio <= 0.01:
                continue
            if (
                ratio > 0.1
                and ratio < 5
                and math.floor(math.log10(assistant_number))
                == math.floor(math.log10(tool_number))
            ):
                has_correct = any(
                    abs(number - tool_number) / abs(tool_number) <= 0.01
                    for number in assistant_numbers
                )
                if not has_correct:
                    return _verdict("DISTORTED", "high", "significant_number_mismatch")

    for key, string_value in tool_strings.items():
        if re.search(re.escape(key), assistant_text, re.I) is None:
            continue
        if string_value in assistant_text:
            continue
        value_words = [word for word in re.split(r"[\s-]+", string_value) if len(word) > 2]
        if value_words and not any(word.lower() in assistant_text.lower() for word in value_words):
            return _verdict("DISTORTED", "low", "values_not_referenced_needs_llm")

    all_values_present = True
    for value in extracted:
        if value["kind"] == "string" and len(value["value"]) >= 3:
            if value["value"].lower() not in assistant_text.lower():
                all_values_present = False
        elif value["kind"] == "number":
            number = _parse_number(value["value"])
            if number is not None and number > 1:
                if not any(abs(item - number) / abs(number) <= 0.01 for item in assistant_numbers):
                    all_values_present = False

    if all_values_present:
        if tool_numbers:
            return _verdict("GROUNDED", "high", "numbers_within_tolerance")
        return _verdict("GROUNDED", "high", "all_tool_values_verified")
    return _verdict("GROUNDED", "low", "values_not_referenced_needs_llm")


def _detect_ungrounded_file_claims(assistant_text: str) -> dict[str, str]:
    if any(pattern.search(assistant_text) for pattern in _HONEST_UNCERTAINTY_PATTERNS):
        return _verdict("GROUNDED", "high", "honest_uncertainty")
    if any(pattern.search(assistant_text) for pattern in _UNGROUNDED_FILE_CLAIM_PATTERNS):
        return _verdict(
            "FABRICATED",
            "high",
            "explicit_file_config_claim_without_read_tool",
        )
    if re.search(r"\d{3,}|[\"'][^\"']{5,}[\"']|\b\d+\.\d+\b", assistant_text):
        return _verdict(
            "GROUNDED",
            "low",
            "specific_details_without_file_claim_needs_llm",
        )
    return _verdict("GROUNDED", "high", "general_knowledge_or_no_specific_claims")


def _extract_tool_output_values(
    tool_outputs: tuple[FactGroundingToolOutput, ...],
) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for output in tool_outputs:
        try:
            parsed = json.loads(output.raw_output)
        except json.JSONDecodeError:
            values.extend(_extract_text_key_values(output.raw_output))
            continue
        if not isinstance(parsed, Mapping):
            continue
        for key, value in parsed.items():
            if isinstance(value, str) and len(value) >= 2:
                values.append({"key": str(key), "value": value, "kind": "string"})
            elif isinstance(value, int | float) and not isinstance(value, bool) and value != 0:
                values.append({"key": str(key), "value": str(value), "kind": "number"})
    return values


def _extract_text_key_values(raw_output: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for match in re.finditer(r"[\"']?(\w+)[\"']?\s*[:=]\s*[\"']?([^\"'\n,}]+)", raw_output):
        key = match.group(1)
        value = match.group(2).strip()
        if _parse_number(value) is not None:
            values.append({"key": key, "value": value, "kind": "number"})
        elif len(value) >= 2:
            values.append({"key": key, "value": value, "kind": "string"})
    return values


def _parse_number(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    if math.isfinite(parsed):
        return parsed
    return None


def _verdict(
    verdict: FactGroundingVerdict,
    confidence: FactGroundingConfidence,
    reason_code: FactGroundingReasonCode,
) -> dict[str, str]:
    return {"verdict": verdict, "confidence": confidence, "reasonCode": reason_code}


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("fact grounding fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("fact grounding fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _PRODUCTION_PATH_RE.search(value):
            raise ValueError("fact grounding fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_RAW_TOKENS):
            raise ValueError("fact grounding fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized_key = _normalize_key(key)
            if nested_value is True and normalized_key in _FORBIDDEN_TRUE_KEYS:
                raise ValueError("fact grounding fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _PRODUCTION_PATH_RE.search(rendered):
        raise ValueError("fact grounding public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("fact grounding public snapshot contains unsafe data")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("fact grounding fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("fact grounding mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("fact grounding fixture values must be JSON-compatible")


def _normalize_key(key: object) -> str:
    if not isinstance(key, str):
        raise ValueError("fact grounding mappings must use string keys")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


__all__ = [
    "FactGroundingVerifierAttachmentFlags",
    "FactGroundingVerifierCase",
    "FactGroundingVerifierFixture",
    "FactGroundingVerifierProjection",
    "FactGroundingToolOutput",
    "deterministic_fact_grounding_verdict",
    "load_fact_grounding_verifier_fixture",
    "project_fact_grounding_verifier_fixture",
]
