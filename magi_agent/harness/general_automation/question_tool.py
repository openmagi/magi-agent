"""Track 19 PR7 — blocking General-Automation ``question`` tool.

This module ports OpenCode's blocking ``question`` (the "don't guess" lever) to
the ``general`` agent role — but, unlike OpenCode's *ephemeral* question, magi's
leaves a control/evidence trail. A model-callable ``question`` tool asks the
user a structured clarifying question (header + question text + options +
implicit free-text), BLOCKS the turn (returns a ``pending_control_request`` /
``needs_approval`` result), and resumes on the user's reply.

It **extends the EXISTING** General-Automation pack — it does NOT add a new
pack or invent a new control/resume mechanism:

* the question becomes a
  :class:`~magi_agent.harness.general_automation.control_projection.GeneralAutomationControlProjection`
  built by
  :func:`~magi_agent.harness.general_automation.control_projection.build_general_automation_control_projection`
  with ``controlType="approval_required"`` (``executionAllowed`` stays
  ``Literal[False]``, authority flags untouched), and
* the reply resumes through the existing control store
  (:class:`~magi_agent.runtime.control.ControlRequestStore`) by resolving a
  ``user_question`` request keyed on the projection's ``resumeRef`` — the same
  ``pending → reply → resume`` flow used elsewhere. No new resume primitive.

Activation requires BOTH (mirroring PR2/PR6):

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* ``agent_role == "general"``.

When inactive — non-general role or flag-OFF — the tool is inert: the classifier
returns an inactive bypass outcome and the handler returns a ``blocked`` no-op,
so flag-OFF / non-general behavior is byte-identical to ``main``.

**Leak-safety.** The control projection stores only digests/refs: the question
text and option descriptions are folded into ``payloadDigest`` /
``metadataDigest`` (sha256) and never stored raw. The only human-readable values
surfaced are the option *labels*, run through the transport secret scrubber.

Wiring seam: like PR3's ``completion_repair_decision``, PR5's max-steps brake,
and PR6's constraint re-injection, the production runner does not yet route a
``general`` tool call named :data:`GENERAL_AUTOMATION_QUESTION_TOOL_NAME`
through :func:`general_automation_question_handler` and back through
:func:`resume_general_automation_question`. The manifest, handler, and resume
helper are declared and exercised by tests, ready for the runner to attach —
without inventing a new pack or resume mechanism.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjection,
    GeneralAutomationControlProjectionRequest,
    build_general_automation_control_projection,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.transport.tool_preview import sanitize_tool_preview

if TYPE_CHECKING:
    from magi_agent.runtime.control import ControlRequestStore, ControlRequestStoreResult
    from magi_agent.tools.manifest import ToolManifest


#: Name of the GA blocking-question tool. Referenced by the resolved ``general``
#: pack ``tools`` tuple in ``harness/resolved.py`` and the ``automation.plan``
#: preset ``tool_categories`` (category ``user_question``).
GENERAL_AUTOMATION_QUESTION_TOOL_NAME = "GeneralAutomationQuestion"

#: GA preset tool category that surfaces this tool.
GENERAL_AUTOMATION_QUESTION_CATEGORY = "user_question"

_GA_ROLE = "general"
_POLICY_REF = "policy:general-automation:user-question"
_RESUME_REF_PREFIX = "resume:general-automation-question:"
_SUBJECT_REF_PREFIX = "subject:general-automation-question:"
_MAX_OPTIONS = 12
_MAX_LABEL_CHARS = 120

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

QuestionGateDecision = Literal["allow", "ask"]


class GeneralAutomationQuestionOption(BaseModel):
    """A single structured choice (mirrors OpenCode's ``question`` option)."""

    model_config = _MODEL_CONFIG

    label: str
    description: str = ""

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("option label must be non-empty")
        return cleaned


class GeneralAutomationQuestion(BaseModel):
    """A structured clarifying question (header + text + options + free-text)."""

    model_config = _MODEL_CONFIG

    header: str
    question: str
    options: tuple[GeneralAutomationQuestionOption, ...] = ()
    multiple: bool = False

    @field_validator("header", "question")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("header and question must be non-empty")
        return cleaned

    @field_validator("options")
    @classmethod
    def _validate_options(
        cls,
        value: tuple[GeneralAutomationQuestionOption, ...],
    ) -> tuple[GeneralAutomationQuestionOption, ...]:
        if len(value) > _MAX_OPTIONS:
            raise ValueError(f"at most {_MAX_OPTIONS} options are allowed")
        return value

    @property
    def free_text_allowed(self) -> Literal[True]:
        """A free-text reply is always permitted alongside the options."""
        return True


@dataclass(frozen=True)
class GeneralAutomationQuestionOutcome:
    """Result of classifying a pending ``question`` tool call.

    ``active`` is ``False`` whenever the tool is inert (flag-OFF or
    non-general). In that case ``decision`` is ``allow`` and no control
    projection is produced, so callers proceed unchanged. When active the
    ``decision`` is ``ask`` and the turn must block on ``control_projection``.
    """

    active: bool
    decision: QuestionGateDecision
    control_projection: GeneralAutomationControlProjection | None = None
    option_labels: tuple[str, ...] = ()
    resume_ref: str | None = None
    idempotency_key: str | None = None
    reason: str | None = None


_BYPASS_OUTCOME = GeneralAutomationQuestionOutcome(active=False, decision="allow")


def classify_general_automation_question(
    question: GeneralAutomationQuestion,
    context: ToolContext,
    *,
    env: Mapping[str, str] | None = None,
) -> GeneralAutomationQuestionOutcome:
    """Classify a pending ``question`` call. Pure bypass when inert.

    Active only when ``MAGI_GA_LIVE_ENABLED`` is truthy AND the role is
    ``general``. When active, builds an ``approval_required`` control projection
    carrying the question (as digests) plus a resume ref, and returns an
    ``ask`` (blocking) outcome. No tool execution / side effects.
    """
    if not general_automation_live_enabled(env):
        return _BYPASS_OUTCOME
    if _agent_role(context) != _GA_ROLE:
        return _BYPASS_OUTCOME

    payload_digest = _question_payload_digest(question)
    subject_ref = _SUBJECT_REF_PREFIX + _short(payload_digest)
    resume_ref = _RESUME_REF_PREFIX + _short(
        _digest(
            {
                "sessionKey": context.session_key or "",
                "turnId": context.turn_id or "",
                "payloadDigest": payload_digest,
            }
        )
    )
    metadata = {
        "questionRefs": {
            "optionCount": len(question.options),
            "multiple": question.multiple,
            "freeTextAllowed": True,
        },
    }
    request = GeneralAutomationControlProjectionRequest(
        controlType="approval_required",
        subjectRef=subject_ref,
        policyRef=_POLICY_REF,
        payloadDigest=payload_digest,
        reasonCodes=("general_automation_user_question",),
        resumeRef=resume_ref,
        metadata=metadata,
    )
    projection = build_general_automation_control_projection(request)
    return GeneralAutomationQuestionOutcome(
        active=True,
        decision="ask",
        control_projection=projection,
        option_labels=_safe_labels(question.options),
        resume_ref=resume_ref,
        idempotency_key=projection.control_ref,
        reason="general_automation_user_question",
    )


def general_automation_question_handler(
    arguments: Mapping[str, object],
    context: ToolContext,
) -> ToolResult:
    """Tool handler: block the turn on a structured clarifying question.

    Returns ``needs_approval`` (a ``pending_control_request``) carrying the
    ``approval_required`` control projection when active; otherwise (flag-OFF /
    non-general / malformed input) a ``blocked`` no-op so flag-OFF behaves like
    ``main``. No tool execution side effects either way.
    """
    base_metadata: dict[str, object] = {
        "toolName": GENERAL_AUTOMATION_QUESTION_TOOL_NAME,
        "permissionClass": "meta",
        "dangerous": False,
        "mutatesWorkspace": False,
        "generalAutomationQuestion": True,
    }

    try:
        question = _question_from_arguments(arguments)
    except (ValueError, TypeError):
        return ToolResult(
            status="blocked",
            errorCode="general_automation_question_invalid",
            errorMessage="question input did not match the question schema",
            metadata={**base_metadata, "reason": "general_automation_question_invalid"},
        )

    outcome = classify_general_automation_question(question, context)
    if not outcome.active or outcome.control_projection is None:
        return ToolResult(
            status="blocked",
            metadata={
                **base_metadata,
                "reason": "general_automation_question_inert",
            },
        )

    return ToolResult(
        status="needs_approval",
        metadata={
            **base_metadata,
            "reason": outcome.reason or "general_automation_user_question",
            "pendingControlRequest": True,
            "controlProjection": outcome.control_projection.public_projection(),
            "optionLabels": list(outcome.option_labels),
            "resumeRef": outcome.resume_ref,
        },
    )


def resume_general_automation_question(
    outcome: GeneralAutomationQuestionOutcome,
    *,
    store: ControlRequestStore,
    session_key: str,
    turn_id: str | None,
    answer: str,
    now: int | float,
    timeout_ms: int | float,
    channel_name: str | None = None,
) -> GeneralAutomationQuestionResume:
    """Resume a blocked question via the existing control-store resume flow.

    Reuses :class:`~magi_agent.runtime.control.ControlRequestStore`: opens a
    ``user_question`` request keyed on the outcome's resume ref / control ref
    (idempotent), then resolves it with ``decision="answered"`` + the user's
    answer. The returned ``resume_ref`` equals the projection's ``resumeRef`` —
    that is the resume linkage. No new resume mechanism is introduced.
    """
    if outcome.control_projection is None or outcome.resume_ref is None:
        raise ValueError("outcome is not a blocking question outcome")

    # TODO(sanitized-answer): The ``answer`` returned from
    # ``store.resolve_request(...)`` (assigned to ``resolved.answer`` below) is
    # ALREADY sanitized by the control store — a 240-char cap is applied and
    # filesystem paths / secret tokens are redacted store-wide before the record
    # is committed.  This means a user answer that contains a filesystem path
    # (e.g. ``/home/user/.ssh/id_rsa``) or a bearer token will arrive here in
    # redacted form.  Callers that consume ``GeneralAutomationQuestionResume.answer``
    # must account for this: if full-fidelity answer text is required (e.g. for
    # a path the user intentionally provided), carry it over a dedicated
    # out-of-band channel rather than relying on the sanitized store record.
    idempotency_key = outcome.idempotency_key or outcome.control_projection.control_ref
    created = store.create_user_question_request(
        session_key=session_key,
        turn_id=turn_id,
        channel_name=channel_name,
        source="turn",
        prompt=outcome.resume_ref,
        proposed_input=None,
        idempotency_key=idempotency_key,
        now=now,
        timeout_ms=timeout_ms,
    )
    request_id = created.record.request_id
    existing = store.get_terminal(request_id)
    if existing is not None and existing.decision == "answered":
        resolved = existing
    else:
        resolved = store.resolve_request(
            request_id,
            decision="answered",
            now=now,
            answer=answer,
        ).record
    return GeneralAutomationQuestionResume(
        request_id=request_id,
        resume_ref=outcome.resume_ref,
        control_ref=outcome.control_projection.control_ref,
        answer=resolved.answer,
    )


@dataclass(frozen=True)
class GeneralAutomationQuestionResume:
    """Resume linkage between a question control and the user's reply."""

    request_id: str
    resume_ref: str
    control_ref: str
    answer: str | None


def general_automation_question_manifest() -> "ToolManifest":
    """Manifest for the GA blocking-question tool.

    ``meta`` permission (no mutation, not dangerous), available in both modes,
    disabled by default at the manifest level — the live flag gate
    (:func:`general_automation_live_enabled`) is the authority for activation.
    """
    # Deferred import: ToolManifest pulls magi_agent.tools.manifest →
    # magi_agent.transport.  Keeping it local lets resolved.py import the tool
    # NAME constant without paying the transport cost at module load.
    from magi_agent.tools.manifest import ToolManifest, ToolSource

    return ToolManifest(
        name=GENERAL_AUTOMATION_QUESTION_TOOL_NAME,
        description=(
            "Ask the user a structured clarifying question (options + free text) "
            "and block the turn until they reply. Use instead of guessing."
        ),
        kind="native",
        source=ToolSource(
            kind="builtin",
            package="magi_agent.harness.general_automation",
        ),
        permission="meta",
        inputSchema=_question_input_schema(),
        availableInModes=("plan", "act"),
        tags=("general-automation", "user", "question", "meta"),
        parallel_safety="unsafe",
        timeoutMs=600_000,
        enabled_by_default=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question_from_arguments(arguments: Mapping[str, object]) -> GeneralAutomationQuestion:
    raw_options = arguments.get("options")
    options: list[GeneralAutomationQuestionOption] = []
    if isinstance(raw_options, Sequence) and not isinstance(raw_options, str | bytes):
        for item in raw_options:
            if not isinstance(item, Mapping):
                raise TypeError("each option must be an object")
            label = item.get("label")
            description = item.get("description", "")
            options.append(
                GeneralAutomationQuestionOption(
                    label=str(label) if label is not None else "",
                    description=str(description) if description is not None else "",
                )
            )
    elif raw_options is not None:
        raise TypeError("options must be a list of objects")

    header = arguments.get("header")
    question = arguments.get("question")
    multiple = arguments.get("multiple", False)
    return GeneralAutomationQuestion(
        header=str(header) if header is not None else "",
        question=str(question) if question is not None else "",
        options=tuple(options),
        multiple=bool(multiple),
    )


def _question_payload_digest(question: GeneralAutomationQuestion) -> str:
    return _digest(
        {
            "header": question.header,
            "question": question.question,
            "options": [
                {"label": option.label, "description": option.description}
                for option in question.options
            ],
            "multiple": question.multiple,
            "freeTextAllowed": True,
        }
    )


def _safe_labels(
    options: Sequence[GeneralAutomationQuestionOption],
) -> tuple[str, ...]:
    return tuple(
        sanitize_tool_preview(option.label)[:_MAX_LABEL_CHARS] for option in options
    )


def _agent_role(context: ToolContext) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return value.strip().casefold().replace("-", "_")
    return ""


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:24]


def _question_input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "header": {"type": "string", "maxLength": 240},
            "question": {"type": "string", "maxLength": 2000},
            "options": {
                "type": "array",
                "minItems": 0,
                "maxItems": _MAX_OPTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "maxLength": _MAX_LABEL_CHARS},
                        "description": {"type": "string", "maxLength": 480},
                    },
                    "required": ["label"],
                    "additionalProperties": False,
                },
            },
            "multiple": {"type": "boolean"},
        },
        "required": ["header", "question"],
        "additionalProperties": False,
    }


__all__ = [
    "GENERAL_AUTOMATION_QUESTION_CATEGORY",
    "GENERAL_AUTOMATION_QUESTION_TOOL_NAME",
    "GeneralAutomationQuestion",
    "GeneralAutomationQuestionOption",
    "GeneralAutomationQuestionOutcome",
    "GeneralAutomationQuestionResume",
    "classify_general_automation_question",
    "general_automation_question_handler",
    "general_automation_question_manifest",
    "resume_general_automation_question",
]
