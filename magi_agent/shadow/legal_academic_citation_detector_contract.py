from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


CitationSignal: TypeAlias = Literal[
    "kr_case_number",
    "kr_legal_cue",
    "statute_article",
    "doi",
    "arxiv",
]
CitationClassification: TypeAlias = Literal["legal", "academic", "mixed", "none"]
SourceFamily: TypeAlias = Literal["korean_court", "korean_statute", "doi", "arxiv"]
CitationCategory: TypeAlias = Literal[
    "kr_case_number",
    "kr_legal_cue",
    "statute_article",
    "doi",
    "arxiv",
    "mixed",
    "none",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_REQUIRED_CATEGORIES = set(CitationCategory.__args__)  # type: ignore[attr-defined]
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_citationsecret",
    "sk-citation-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "private raw page",
    "hidden reasoning",
)
_FORBIDDEN_TRUE_KEYS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "agent_memory_imported",
        "agent_evaluator_attached",
        "browser_executed",
        "browser_attached",
        "eval_attached",
        "evidence_block_enabled",
        "evaluation_attached",
        "live_tool_dispatched",
        "memory_provider_called",
        "memory_provider_attached",
        "prompt_gate_attached",
        "provider_attached",
        "route_or_api_attached",
        "runner_attached",
        "search_attached",
        "source_fetched",
        "tool_attached",
        "tool_host_dispatched",
        "tool_provider_attached",
        "traffic_attached",
        "web_search_executed",
    }
)
_CASE_NUMBER_RE = re.compile(
    r"(?<!\d)\d{4}\s*(?:다|도|누|구합|구단|마|므|카합|카단|카기|헌가|헌마)\s*\d+(?!\d)"
)
_LEGAL_CUE_RE = re.compile(
    r"(?:대법원|헌법재판소|서울고등법원|서울중앙지방법원|판결|결정|선고)"
)
_STATUTE_ARTICLE_RE = re.compile(
    r"(?:[가-힣A-Za-z]+(?:법|령|규칙)\s*)?제\s*\d+\s*조(?:의\s*\d+)?"
)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"\barXiv:\d{4}\.\d{4,5}(?:v\d+)?\b|\barxiv\.org/abs/\d{4}\.\d{4,5}(?:v\d+)?\b",
    re.IGNORECASE,
)


class LegalAcademicCitationDetectorAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    web_search_executed: Literal[False] = Field(default=False, alias="webSearchExecuted")
    source_fetched: Literal[False] = Field(default=False, alias="sourceFetched")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    evaluation_attached: Literal[False] = Field(default=False, alias="evaluationAttached")
    prompt_gate_attached: Literal[False] = Field(default=False, alias="promptGateAttached")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

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
        "web_search_executed",
        "source_fetched",
        "browser_executed",
        "toolhost_dispatched",
        "evaluation_attached",
        "prompt_gate_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class LegalAcademicCitationDetectorCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: CitationCategory
    input_text: str = Field(alias="inputText")
    prompt_text: str = Field(alias="promptText")
    classification: CitationClassification
    expected_signals: tuple[CitationSignal, ...] = Field(alias="expectedSignals")
    required_source_families: tuple[SourceFamily, ...] = Field(
        alias="requiredSourceFamilies",
    )
    metadata: dict[str, object]
    attachment_flags: LegalAcademicCitationDetectorAttachmentFlags = Field(
        alias="attachmentFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.case_id.strip():
            raise ValueError("citation detector case requires caseId")
        if not self.input_text.strip():
            raise ValueError("citation detector case requires inputText")
        if not self.prompt_text.strip():
            raise ValueError("citation detector case requires promptText")
        detected = detect_citation_signals(self.input_text)
        if detected != self.expected_signals:
            raise ValueError("citation detector expectedSignals do not match inputText")
        classification = classify_citation_signals(detected)
        if classification != self.classification:
            raise ValueError("citation detector classification does not match signals")
        families = required_source_families_for_signals(detected)
        if families != self.required_source_families:
            raise ValueError("citation detector source families do not match signals")
        if self.category == "mixed" and self.classification != "mixed":
            raise ValueError("mixed citation detector case must classify as mixed")
        if self.category == "none" and (self.expected_signals or self.classification != "none"):
            raise ValueError("none citation detector case must have no signals")
        if self.category not in {"mixed", "none"} and self.category not in self.expected_signals:
            raise ValueError("citation detector category must be represented in signals")
        return self


class LegalAcademicCitationDetectorFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["legalAcademicCitationDetectorFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    target_runtime: Literal["python-adk-future"] = Field(alias="targetRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    attachment_flags: LegalAcademicCitationDetectorAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    cases: tuple[LegalAcademicCitationDetectorCase, ...]

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
            raise ValueError("citation detector caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("citation detector fixture is missing required categories")
        return self


class LegalAcademicCitationDetectorProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    attachment_flags: LegalAcademicCitationDetectorAttachmentFlags = Field(
        alias="attachmentFlags",
    )
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_classification: dict[str, int] = Field(alias="byClassification")
    by_signal: dict[str, int] = Field(alias="bySignal")
    by_required_source_family: dict[str, int] = Field(alias="byRequiredSourceFamily")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def detect_citation_signals(text: str) -> tuple[CitationSignal, ...]:
    signals: list[CitationSignal] = []
    checks: tuple[tuple[CitationSignal, re.Pattern[str]], ...] = (
        ("kr_case_number", _CASE_NUMBER_RE),
        ("kr_legal_cue", _LEGAL_CUE_RE),
        ("statute_article", _STATUTE_ARTICLE_RE),
        ("doi", _DOI_RE),
        ("arxiv", _ARXIV_RE),
    )
    for signal, pattern in checks:
        if pattern.search(text):
            signals.append(signal)
    return tuple(signals)


def classify_citation_signals(
    signals: tuple[CitationSignal, ...],
) -> CitationClassification:
    has_legal = any(signal in {"kr_case_number", "kr_legal_cue", "statute_article"} for signal in signals)
    has_academic = any(signal in {"doi", "arxiv"} for signal in signals)
    if has_legal and has_academic:
        return "mixed"
    if has_legal:
        return "legal"
    if has_academic:
        return "academic"
    return "none"


def required_source_families_for_signals(
    signals: tuple[CitationSignal, ...],
) -> tuple[SourceFamily, ...]:
    families: list[SourceFamily] = []
    family_by_signal: dict[CitationSignal, SourceFamily] = {
        "kr_case_number": "korean_court",
        "kr_legal_cue": "korean_court",
        "statute_article": "korean_statute",
        "doi": "doi",
        "arxiv": "arxiv",
    }
    for signal in signals:
        family = family_by_signal[signal]
        if family not in families:
            families.append(family)
    return tuple(families)


def load_legal_academic_citation_detector_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> LegalAcademicCitationDetectorFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return LegalAcademicCitationDetectorFixture.model_validate(payload)


def project_legal_academic_citation_detector_fixture(
    fixture: LegalAcademicCitationDetectorFixture | Mapping[str, Any],
) -> LegalAcademicCitationDetectorProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    case_snapshots: dict[str, dict[str, object]] = {}
    classification_counts: Counter[str] = Counter()
    signal_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()

    for case in safe_fixture.cases:
        classification_counts[case.classification] += 1
        signal_counts.update(case.expected_signals)
        family_counts.update(case.required_source_families)
        snapshot = {
            "caseId": case.case_id,
            "category": case.category,
            "classification": case.classification,
            "detectedSignals": case.expected_signals,
            "requiredSourceFamilies": case.required_source_families,
            "metadataOnly": True,
            "localDiagnostic": True,
        }
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot

    return LegalAcademicCitationDetectorProjection(
        fixtureId=safe_fixture.fixture_id,
        localDiagnostic=True,
        metadataOnly=True,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byClassification=dict(classification_counts),
        bySignal=dict(signal_counts),
        byRequiredSourceFamily=dict(family_counts),
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: LegalAcademicCitationDetectorFixture | Mapping[str, Any],
) -> LegalAcademicCitationDetectorFixture:
    if isinstance(fixture, LegalAcademicCitationDetectorFixture):
        return LegalAcademicCitationDetectorFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return LegalAcademicCitationDetectorFixture.model_validate(fixture)


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
        raise ValueError("citation detector fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("citation detector fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _PRODUCTION_PATH_RE.search(value):
            raise ValueError("citation detector fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("citation detector fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized_key = _normalize_key(key)
            if nested_value is True and normalized_key in _FORBIDDEN_TRUE_KEYS:
                raise ValueError("citation detector fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _PRODUCTION_PATH_RE.search(rendered):
        raise ValueError("citation detector public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("citation detector public snapshot contains unsafe data")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("citation detector fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("citation detector mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("citation detector fixture values must be JSON-compatible")


def _normalize_key(key: object) -> str:
    if not isinstance(key, str):
        raise ValueError("citation detector mappings must use string keys")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


__all__ = [
    "LegalAcademicCitationDetectorAttachmentFlags",
    "LegalAcademicCitationDetectorCase",
    "LegalAcademicCitationDetectorFixture",
    "LegalAcademicCitationDetectorProjection",
    "classify_citation_signals",
    "detect_citation_signals",
    "load_legal_academic_citation_detector_fixture",
    "project_legal_academic_citation_detector_fixture",
    "required_source_families_for_signals",
]
