"""Output-Contract Adherence Gate — general format-discipline gate for final answers.

Default-OFF.  Three modes: audit (observe only), enforce (block on violation),
llm_repair (attempt minimal LLM-assisted format correction).

Anti-overfitting firewall: this module MUST NOT import from any benchmark
scoring layer.  Any PR that adds a scorer import is a violation.

Sibling of magi_agent.research.final_projection_gate (evidence integrity gate).
This gate answers a different question: "Is the final answer in the shape the
task asked for?" — not "Is it supported by evidence?"

Environment variable: MAGI_OUTPUT_CONTRACT_GATE_MODE
  values: off | audit | enforce | llm_repair   (default: off)
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

OutputContractType = Literal[
    "unspecified",
    "number",
    "integer",
    "string",
    "text",
    "list",
    "list_of_numbers",
    "filename",
    "code",
    "boolean",
]

OutputContractGateMode = Literal["off", "audit", "enforce", "llm_repair"]

OutputContractGateStatus = Literal[
    "skipped",
    "passed",
    "audit",
    "format_violation",
    "repaired",
    "repair_refused",
]

OutputContractReasonCode = Literal[
    "output_contract_gate_off",
    "output_contract_passed",
    "type_mismatch",
    "concise_violation",
    "article_present",
    "trailing_period_on_number",
    "length_violation",
    "list_count_violation",
    "abbreviation_present",
]


# ---------------------------------------------------------------------------
# Constants / patterns
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)

_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

# Scene-heading scaffolding: "INT.", "EXT.", "INT/EXT.", day notation at end
_SCENE_HEADING_RE = re.compile(
    r"^\s*(?:INT|EXT|INT/EXT|I/E)\.?\s*[./]?\s*",
    re.IGNORECASE,
)
_SCENE_DAY_NOTATION_RE = re.compile(
    r"\s*[-–]\s*(?:DAY|NIGHT|DAWN|DUSK|CONTINUOUS|LATER|MOMENTS\s+LATER)\b.*$",
    re.IGNORECASE,
)

# Scaffolding prefixes that violate conciseness
_SCAFFOLDING_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"FINAL\s+ANSWER\s*[:\-]|"
    r"THE\s+ANSWER\s+IS\s*[:\-]?|"
    r"ANSWER\s*[:\-]|"
    r"RESULT\s*[:\-]|"
    r"OUTPUT\s*[:\-]"
    r")\s*",
    re.IGNORECASE,
)

# Leading articles
_ARTICLE_RE = re.compile(r"^\s*(?:the|a|an)\s+", re.IGNORECASE)

# Common abbreviation markers for forbid_abbreviations (letters followed by period inside words)
_ABBREVIATION_RE = re.compile(r"\b[A-Z]{1,5}\.", re.IGNORECASE)

# Boolean accepted values
_BOOLEAN_VALUES: frozenset[str] = frozenset({
    "yes", "no", "true", "false", "1", "0",
})

# Jaccard threshold per type — for "string" we use a lower bar because reformatting
# may change many tokens, but the key noun must survive.
_JACCARD_THRESHOLD: dict[str, float] = {
    "string": 0.3,
    "text": 0.5,
    "list": 0.5,
    "list_of_numbers": 0.5,
    "filename": 0.5,
    "code": 0.4,
    "boolean": 0.0,  # irrelevant — boolean uses value identity
    "number": 0.0,   # irrelevant — number uses numeric identity
    "integer": 0.0,  # irrelevant — integer uses numeric identity
    "unspecified": 0.0,
}

_ADK_USAGE_NOTES = (
    "Output-contract gate metadata only; no ADK Runner, live provider, "
    "browser, memory write, channel delivery, or ToolHost execution is attached."
)


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class _OutputContractModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for output contract gate models")

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


# ---------------------------------------------------------------------------
# Execution posture
# ---------------------------------------------------------------------------


class OutputContractGateExecutionPosture(_OutputContractModel):
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    channel_delivery_allowed: Literal[False] = Field(default=False, alias="channelDeliveryAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")


# ---------------------------------------------------------------------------
# OutputContract model
# ---------------------------------------------------------------------------


class OutputContract(_OutputContractModel):
    """Typed declaration of what the final answer must look like (shape/discipline).

    The contract is separate from *what* the answer says — it describes the expected
    format.  Supplied by the task, recipe, or harness; not by the gate itself.

    For GAIA: harness constructs OutputContract(type="string", concise=True,
    forbid_articles=True, forbid_abbreviations=True) from the GAIA system prompt
    English rules — without importing scorer.py.
    """

    contract_id: str = Field(alias="contract_id")
    type: OutputContractType = "unspecified"
    concise: bool = False
    max_items: int | None = None
    min_items: int | None = None
    forbid_units: bool = False
    forbid_articles: bool = False
    forbid_abbreviations: bool = False
    max_chars: int | None = None
    min_chars: int | None = None
    allow_punctuation: bool = True

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    @field_validator("contract_id")
    @classmethod
    def _validate_contract_id(cls, value: str) -> str:
        return _public_ref(value, "contract_id")

    @field_validator("max_items", "min_items", "max_chars", "min_chars", mode="before")
    @classmethod
    def _validate_non_negative(cls, value: object) -> object:
        if value is not None and isinstance(value, int) and value < 0:
            raise ValueError("count/length constraints must be non-negative")
        return value


# ---------------------------------------------------------------------------
# Receipt model for Stage B model calls
# ---------------------------------------------------------------------------


class OutputContractModelCallReceipt(_OutputContractModel):
    """Audit receipt for an LLM repair call.  Follows the ProviderReceipt pattern."""

    gate_id: str
    contract_id: str
    candidate_digest: str
    repaired_digest: str
    similarity_score: float | None
    repair_accepted: bool
    refusal_reason: str | None = None

    @field_validator("candidate_digest", "repaired_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256 hex")
        return value

    @field_validator("similarity_score", mode="before")
    @classmethod
    def _validate_similarity(cls, value: object) -> object:
        if value is not None:
            score = float(value)
            if not (0.0 <= score <= 1.0):
                raise ValueError("similarity_score must be in [0, 1]")
            return score
        return value


# ---------------------------------------------------------------------------
# Gate request / result
# ---------------------------------------------------------------------------


class OutputContractGateRequest(_OutputContractModel):
    gate_id: str
    mode: OutputContractGateMode = "off"
    candidate_final_answer: str
    contract: OutputContract
    model_provider: Any = Field(default=None, exclude=True)

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
        arbitrary_types_allowed=True,
    )

    @field_validator("gate_id")
    @classmethod
    def _validate_gate_id(cls, value: str) -> str:
        return _public_ref(value, "gate_id")

    @field_validator("candidate_final_answer")
    @classmethod
    def _validate_candidate(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("candidate_final_answer must be non-empty")
        if len(clean) > 10_000:
            raise ValueError("candidate_final_answer must be at most 10000 characters")
        return clean


class OutputContractGateResult(_OutputContractModel):
    """Result of the output-contract gate evaluation."""

    _issued_by_output_contract_gate: bool = PrivateAttr(default=False)

    gate_id: str
    mode: OutputContractGateMode
    status: OutputContractGateStatus
    ok: bool
    reason_codes: tuple[OutputContractReasonCode, ...]
    candidate_digest: str
    conformed_digest: str
    conformed_answer: str | None = None
    repair_applied: bool = False
    similarity_score: float | None = None
    model_call_receipt: OutputContractModelCallReceipt | None = None
    execution_posture: OutputContractGateExecutionPosture = Field(
        default_factory=OutputContractGateExecutionPosture
    )
    adk_usage_notes: str = _ADK_USAGE_NOTES

    @field_validator("gate_id")
    @classmethod
    def _validate_gate_id(cls, value: str) -> str:
        return _public_ref(value, "gate_id")

    @field_validator("candidate_digest", "conformed_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256 hex")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_unique(cls, value: tuple[OutputContractReasonCode, ...]) -> tuple[OutputContractReasonCode, ...]:
        if len(set(value)) != len(value):
            raise ValueError("reason_codes must not contain duplicates")
        return value

    @model_validator(mode="after")
    def _validate_result_shape(self) -> Self:
        if self.status in {"skipped", "passed", "audit"} and not self.ok:
            raise ValueError("skipped/passed/audit results must have ok=True")
        if self.status in {"format_violation", "repair_refused"} and self.ok:
            raise ValueError("format_violation/repair_refused results must have ok=False")
        return self


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_output_contract_gate(
    request: OutputContractGateRequest,
) -> OutputContractGateResult:
    """Evaluate the output-contract gate for a candidate final answer.

    Stages:
      A — deterministic checks (always runs when mode != "off")
      B — LLM repair (only when mode="llm_repair" and Stage A flags violations)

    Returns an OutputContractGateResult; never raises on contract violations
    (violations are recorded in reason_codes, status, and ok).
    """
    parsed = OutputContractGateRequest.model_validate(
        request.model_dump(mode="python", warnings=False)
        | {"model_provider": request.model_provider}
    )
    candidate = parsed.candidate_final_answer
    candidate_digest = _digest_text(candidate)

    if parsed.mode == "off":
        return _build_result(
            gate_id=parsed.gate_id,
            mode=parsed.mode,
            status="skipped",
            ok=True,
            reason_codes=("output_contract_gate_off",),
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
        )

    # Stage A — deterministic
    violations = _stage_a_check(candidate, parsed.contract)

    if not violations:
        return _build_result(
            gate_id=parsed.gate_id,
            mode=parsed.mode,
            status="passed" if parsed.mode != "audit" else "audit",
            ok=True,
            reason_codes=("output_contract_passed",),
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
        )

    # Violations found
    reason_codes = tuple(dict.fromkeys(violations))

    if parsed.mode == "audit":
        return _build_result(
            gate_id=parsed.gate_id,
            mode=parsed.mode,
            status="audit",
            ok=True,
            reason_codes=reason_codes,
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
        )

    if parsed.mode == "enforce":
        return _build_result(
            gate_id=parsed.gate_id,
            mode=parsed.mode,
            status="format_violation",
            ok=False,
            reason_codes=reason_codes,
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
        )

    # mode == "llm_repair"
    provider = parsed.model_provider
    if provider is None:
        return _build_result(
            gate_id=parsed.gate_id,
            mode=parsed.mode,
            status="format_violation",
            ok=False,
            reason_codes=reason_codes,
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
        )

    return _stage_b_repair(parsed, candidate, parsed.contract, reason_codes, candidate_digest, provider)


# ---------------------------------------------------------------------------
# Stage A — deterministic checks
# ---------------------------------------------------------------------------


def _stage_a_check(
    candidate: str,
    contract: OutputContract,
) -> list[OutputContractReasonCode]:
    """Run all deterministic checks.  Returns list of reason codes (may be empty)."""
    violations: list[OutputContractReasonCode] = []

    ctype = contract.type
    stripped = candidate.strip()

    if ctype == "unspecified":
        return []

    # Type conformance
    violations.extend(_check_type_conformance(stripped, ctype))

    # Trailing period on numeric answer
    if ctype in {"number", "integer"}:
        violations.extend(_check_trailing_period(stripped))

    # Conciseness check
    if contract.concise:
        violations.extend(_check_conciseness(stripped, ctype))

    # Article check
    if contract.forbid_articles:
        violations.extend(_check_article(stripped))

    # Abbreviation check
    if contract.forbid_abbreviations:
        violations.extend(_check_abbreviations(stripped))

    # Length constraints
    violations.extend(_check_length(stripped, contract))

    # List item count
    if ctype in {"list", "list_of_numbers"}:
        violations.extend(_check_list_count(stripped, contract))

    return list(dict.fromkeys(violations))


def _check_type_conformance(
    text: str,
    contract_type: OutputContractType,
) -> list[OutputContractReasonCode]:
    if contract_type == "number":
        try:
            float(text.replace(",", "").rstrip("."))
        except ValueError:
            return ["type_mismatch"]
    elif contract_type == "integer":
        try:
            val = float(text.replace(",", "").rstrip("."))
            if val != int(val):
                return ["type_mismatch"]
        except ValueError:
            return ["type_mismatch"]
    elif contract_type == "boolean":
        if text.strip().lower() not in _BOOLEAN_VALUES:
            return ["type_mismatch"]
    elif contract_type == "list_of_numbers":
        parts = [p.strip() for p in text.split(",")]
        for part in parts:
            try:
                float(part.replace(",", "").rstrip("."))
            except ValueError:
                return ["type_mismatch"]
    return []


def _check_trailing_period(text: str) -> list[OutputContractReasonCode]:
    stripped = text.rstrip()
    # A trailing period on a number-like answer (e.g. "42.")
    if stripped.endswith("."):
        core = stripped[:-1].strip()
        try:
            float(core.replace(",", ""))
            return ["trailing_period_on_number"]
        except ValueError:
            pass
    return []


def _check_conciseness(text: str, contract_type: OutputContractType) -> list[OutputContractReasonCode]:
    """Detect scaffolding patterns that violate a concise contract."""
    # Scene-heading prefix: INT. / EXT.
    if _SCENE_HEADING_RE.match(text):
        return ["concise_violation"]
    # Scaffolding prefixes like "FINAL ANSWER:", "The answer is:"
    if _SCAFFOLDING_PREFIX_RE.match(text):
        return ["concise_violation"]
    # Day notation at the end of a scene heading (text contains " - DAY")
    if _SCENE_DAY_NOTATION_RE.search(text):
        return ["concise_violation"]
    return []


def _check_article(text: str) -> list[OutputContractReasonCode]:
    if _ARTICLE_RE.match(text):
        return ["article_present"]
    return []


def _check_abbreviations(text: str) -> list[OutputContractReasonCode]:
    """Flag if the text contains likely abbreviations (X. pattern inside words)."""
    # Simple heuristic: matches like "Dr.", "U.S.", "Mr.", etc.
    # Does not match sentence-ending periods (preceded by a full word)
    abbrev_matches = _ABBREVIATION_RE.findall(text)
    if abbrev_matches:
        return ["abbreviation_present"]
    return []


def _check_length(
    text: str, contract: OutputContract
) -> list[OutputContractReasonCode]:
    violations: list[OutputContractReasonCode] = []
    if contract.max_chars is not None and len(text) > contract.max_chars:
        violations.append("length_violation")
    if contract.min_chars is not None and len(text) < contract.min_chars:
        violations.append("length_violation")
    return list(dict.fromkeys(violations))


def _check_list_count(text: str, contract: OutputContract) -> list[OutputContractReasonCode]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    count = len(parts)
    if contract.max_items is not None and count > contract.max_items:
        return ["list_count_violation"]
    if contract.min_items is not None and count < contract.min_items:
        return ["list_count_violation"]
    return []


# ---------------------------------------------------------------------------
# Stage B — LLM repair
# ---------------------------------------------------------------------------


def _stage_b_repair(
    request: OutputContractGateRequest,
    candidate: str,
    contract: OutputContract,
    reason_codes: tuple[OutputContractReasonCode, ...],
    candidate_digest: str,
    provider: object,
) -> OutputContractGateResult:
    """Attempt minimal LLM-assisted format repair.

    Safety layers:
    1. Semantic similarity guard (Jaccard for strings, numeric identity for numbers).
    2. Stage A re-check: repaired text must pass.
    3. One attempt only.
    """
    repair_prompt = _build_repair_prompt(candidate, contract, reason_codes)

    try:
        repaired_raw: str = provider.complete(repair_prompt)  # type: ignore[union-attr]
    except Exception:
        return _build_result(
            gate_id=request.gate_id,
            mode=request.mode,
            status="repair_refused",
            ok=False,
            reason_codes=reason_codes,
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
            repair_applied=False,
        )

    repaired = repaired_raw.strip()
    repaired_digest = _digest_text(repaired)

    # Semantic preservation guard
    similarity = _semantic_similarity(candidate, repaired, contract.type)
    guard_passed, refusal_reason = _semantic_guard(
        candidate, repaired, contract.type, similarity, concise=contract.concise
    )

    receipt = OutputContractModelCallReceipt(
        gate_id=request.gate_id,
        contract_id=contract.contract_id,
        candidate_digest=candidate_digest,
        repaired_digest=repaired_digest,
        similarity_score=similarity,
        repair_accepted=False,  # optimistic; updated below
        refusal_reason=refusal_reason,
    )

    if not guard_passed:
        return _build_result(
            gate_id=request.gate_id,
            mode=request.mode,
            status="repair_refused",
            ok=False,
            reason_codes=reason_codes,
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
            repair_applied=False,
            similarity_score=similarity,
            model_call_receipt=_receipt_accepted(receipt, False),
        )

    # Stage A re-check on repaired text
    post_violations = _stage_a_check(repaired, contract)
    if post_violations:
        return _build_result(
            gate_id=request.gate_id,
            mode=request.mode,
            status="repair_refused",
            ok=False,
            reason_codes=reason_codes,
            candidate_digest=candidate_digest,
            conformed_digest=candidate_digest,
            repair_applied=False,
            similarity_score=similarity,
            model_call_receipt=_receipt_accepted(receipt, False),
        )

    # Repair accepted
    return _build_result(
        gate_id=request.gate_id,
        mode=request.mode,
        status="repaired",
        ok=True,
        reason_codes=("output_contract_passed",),
        candidate_digest=candidate_digest,
        conformed_digest=repaired_digest,
        conformed_answer=repaired,
        repair_applied=True,
        similarity_score=similarity,
        model_call_receipt=_receipt_accepted(receipt, True),
    )


def _receipt_accepted(receipt: OutputContractModelCallReceipt, accepted: bool) -> OutputContractModelCallReceipt:
    """Return a new receipt with repair_accepted updated (frozen model → rebuild)."""
    return OutputContractModelCallReceipt(
        gate_id=receipt.gate_id,
        contract_id=receipt.contract_id,
        candidate_digest=receipt.candidate_digest,
        repaired_digest=receipt.repaired_digest,
        similarity_score=receipt.similarity_score,
        repair_accepted=accepted,
        refusal_reason=receipt.refusal_reason,
    )


def _build_repair_prompt(
    candidate: str,
    contract: OutputContract,
    reason_codes: tuple[OutputContractReasonCode, ...],
) -> str:
    """Build the focused LLM repair prompt.

    Important design constraints (see spec §4 Decision 2 Layer 2):
    - Never include the original question or reasoning trace.
    - Never ask the model to "answer the question again."
    - Never expose internal reason codes directly — translate to prose.
    - Instruct "format only, not content."
    """
    instructions: list[str] = []
    for code in reason_codes:
        if code == "concise_violation":
            instructions.append(
                "Remove any scene-heading notation (e.g. 'INT.', 'EXT.', 'DAY', 'NIGHT') "
                "or answer-scaffolding prefixes (e.g. 'FINAL ANSWER:', 'The answer is:'). "
                "Return only the core value."
            )
        elif code == "article_present":
            instructions.append(
                "Remove any leading article ('the', 'a', 'an') from the answer."
            )
        elif code == "trailing_period_on_number":
            instructions.append(
                "Remove any trailing period from the numeric answer."
            )
        elif code == "type_mismatch":
            instructions.append(
                f"The answer should be a {contract.type}. "
                "Extract only the value that matches the required type."
            )
        elif code == "length_violation":
            if contract.max_chars is not None:
                instructions.append(
                    f"Shorten the answer to at most {contract.max_chars} characters."
                )
        elif code == "list_count_violation":
            if contract.max_items is not None:
                instructions.append(
                    f"Return at most {contract.max_items} comma-separated items."
                )
        elif code == "abbreviation_present":
            instructions.append(
                "Replace any abbreviations with their full form."
            )

    instructions_text = "\n".join(f"- {instr}" for instr in instructions)
    return (
        f"Reformat the following answer to comply with the format contract below.\n"
        f"Do NOT change what the answer says. Only adjust the format.\n"
        f"Return only the reformatted answer, nothing else.\n"
        f"Do not remove precision-preserving qualifiers.\n\n"
        f"Format requirements:\n{instructions_text}\n\n"
        f"Answer to reformat:\n{candidate}"
    )


# ---------------------------------------------------------------------------
# Semantic preservation guard
# ---------------------------------------------------------------------------


def _semantic_similarity(candidate: str, repaired: str, contract_type: str) -> float:
    """Compute Jaccard token-set similarity between candidate and repaired text.

    For number/integer types, returns 1.0 if the numeric value is identical,
    0.0 otherwise (numeric identity is the real guard — Jaccard is irrelevant).
    """
    if contract_type in {"number", "integer"}:
        # Numeric identity — float comparison
        try:
            cval = float(candidate.strip().replace(",", "").rstrip("."))
            rval = float(repaired.strip().replace(",", "").rstrip("."))
            return 1.0 if cval == rval else 0.0
        except ValueError:
            return 0.0

    c_tokens = _tokenize(candidate)
    r_tokens = _tokenize(repaired)
    if not c_tokens and not r_tokens:
        return 1.0
    intersection = c_tokens & r_tokens
    union = c_tokens | r_tokens
    if not union:
        return 1.0
    return len(intersection) / len(union)


def _semantic_guard(
    candidate: str,
    repaired: str,
    contract_type: str,
    similarity: float,
    *,
    concise: bool = False,
) -> tuple[bool, str | None]:
    """Return (guard_passed, refusal_reason).

    For number/integer: numeric value must be identical (similarity==1.0).
    For others: Jaccard must meet the per-type threshold.
    Also reject if character count drops by more than 50% (potential over-trimming)
    on non-numeric types — UNLESS concise=True, which explicitly allows significant
    trimming (e.g. stripping scene-heading scaffolding from a location answer).
    """
    if contract_type in {"number", "integer"}:
        if similarity < 1.0:
            return False, "numeric_value_changed"
        return True, None

    threshold = _JACCARD_THRESHOLD.get(contract_type, 0.3)
    if similarity < threshold:
        return False, f"jaccard_below_threshold_{threshold}"

    # Character-count over-trimming guard for non-numeric types.
    # Skipped when concise=True because concise contracts explicitly expect the
    # scaffolding (scene headings, answer prefixes) to be stripped, which causes
    # a large character-count reduction by design.
    if not concise and contract_type not in {"number", "integer", "boolean", "unspecified"}:
        c_len = len(candidate.strip())
        r_len = len(repaired.strip())
        if c_len > 0 and r_len < c_len * 0.5:
            return False, "excessive_length_reduction"

    return True, None


def _tokenize(text: str) -> set[str]:
    """Casefold + split into word tokens for Jaccard comparison."""
    return set(re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_result(
    *,
    gate_id: str,
    mode: OutputContractGateMode,
    status: OutputContractGateStatus,
    ok: bool,
    reason_codes: tuple[OutputContractReasonCode, ...],
    candidate_digest: str,
    conformed_digest: str,
    conformed_answer: str | None = None,
    repair_applied: bool = False,
    similarity_score: float | None = None,
    model_call_receipt: OutputContractModelCallReceipt | None = None,
) -> OutputContractGateResult:
    result = OutputContractGateResult(
        gate_id=gate_id,
        mode=mode,
        status=status,
        ok=ok,
        reason_codes=reason_codes,
        candidate_digest=candidate_digest,
        conformed_digest=conformed_digest,
        conformed_answer=conformed_answer,
        repair_applied=repair_applied,
        similarity_score=similarity_score,
        model_call_receipt=model_call_receipt,
    )
    result.__pydantic_private__["_issued_by_output_contract_gate"] = True
    return result


def _digest_text(text: str) -> str:
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id (got {clean!r})")
    return clean


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "OutputContract",
    "OutputContractGateExecutionPosture",
    "OutputContractGateMode",
    "OutputContractGateRequest",
    "OutputContractGateResult",
    "OutputContractGateStatus",
    "OutputContractModelCallReceipt",
    "OutputContractReasonCode",
    "OutputContractType",
    "evaluate_output_contract_gate",
]
