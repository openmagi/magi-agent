from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any

from magi_agent.evidence.event_projection import project_source_ledger_events
from magi_agent.evidence.research_final_gate import (
    ResearchClaimRef,
    ResearchFinalGateRequest,
    ResearchFinalGateResult,
    evaluate_research_final_gate,
)
from magi_agent.evidence.source_ledger import LocalResearchSourceLedger
from magi_agent.research.event_projection import project_citation_audit_rule_events
from magi_agent.runtime.public_events import PublicEvent, rule_check_event


RESEARCH_FIRST_CANARY_ENABLED_ENV = (
    "CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_ENABLED"
)
RESEARCH_FIRST_CANARY_KILL_SWITCH_ENV = (
    "CORE_AGENT_PYTHON_GATE8_RESEARCH_FIRST_CANARY_KILL_SWITCH"
)
RESEARCH_RECIPE_ID = "openmagi.research"

_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})
_SOURCE_TEXT = (
    "The selected research path inspected this local source package in read-only "
    "mode. Workspace, memory, browser, channel, scheduler, and write access stay "
    "disabled for this path. The package is a deterministic source fixture and "
    "does not call live providers, tools, memory, browser, channel delivery, or "
    "workspace mutation."
)
_SAFE_ENV_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


@dataclass(frozen=True)
class ResearchFirstSelectedResponse:
    content: str
    public_events: tuple[PublicEvent, ...]
    metadata: dict[str, object]
    final_gate_result: ResearchFinalGateResult
    draft_gate_result: ResearchFinalGateResult | None = None

    @property
    def event_count(self) -> int:
        return len(self.public_events)


def research_first_selected_canary_active(
    payload: object,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    resolved_env = os.environ if env is None else env
    return (
        _is_true(resolved_env.get(RESEARCH_FIRST_CANARY_ENABLED_ENV))
        and not _is_true(resolved_env.get(RESEARCH_FIRST_CANARY_KILL_SWITCH_ENV))
        and _payload_selects_research_recipe(payload)
    )


def build_research_first_selected_response(
    payload: object,
    *,
    bot_id: str,
    user_id: str,
    environment: str,
    now_ms: int,
    request_digest: str | None = None,
) -> ResearchFirstSelectedResponse:
    if not isinstance(payload, Mapping):
        raise ValueError("research-first payload must be an object")
    if not _payload_selects_research_recipe(payload):
        raise ValueError("research-first payload must select openmagi.research")

    user_text = _extract_last_user_text(payload)
    request_ref = (
        request_digest
        if _is_sha256_digest(request_digest)
        else _sha256_json(_json_safe(payload))
    )
    turn_id = f"turn-research-first-{request_ref.removeprefix('sha256:')[:16]}"
    ledger = LocalResearchSourceLedger(
        ledgerId=f"ledger-research-first-{request_ref.removeprefix('sha256:')[:16]}",
        sessionId=f"session-research-first-{_sha256_text(bot_id + ':' + user_id)[7:23]}",
        turnId=turn_id,
        agentRole="research",
    )
    source_hash = _sha256_text(_SOURCE_TEXT)
    source = ledger.record_source(
        {
            "turnId": turn_id,
            "toolName": "ResearchSourceInspect",
            "toolUseId": f"tool-research-first-{source_hash[7:23]}",
            "evidenceType": "SourceInspection",
            "kind": "external_doc",
            "uri": f"ref:research-first:{source_hash[7:31]}",
            "title": "Research source package",
            "contentHash": source_hash,
            "contentType": "text/plain",
            "trustTier": "official",
            "inspectedAt": now_ms,
            "inspected": True,
            "metadata": {
                "retrievedAt": now_ms,
                "sourceRefDigest": source_hash,
            },
        }
    )
    content = (
        "Research result: The selected request inspected the read-only source "
        "package [src_1]. Workspace, memory, browser, channel, scheduler, and "
        "write access remained disabled [src_1]."
    )
    claims = (
        ResearchClaimRef(claimId="claim:source-inspected", citedRefs=(source.source_id,)),
        ResearchClaimRef(claimId="claim:write-access-disabled", citedRefs=(source.source_id,)),
    )
    draft_gate = _draft_gate_if_unsupported_claim_requested(
        user_text=user_text,
        content=content,
        claims=claims,
        ledger=ledger,
        turn_id=turn_id,
    )
    final_gate = evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-first-selected-readonly",
            turnId=turn_id,
            mode="local_block_intent",
            candidateFinalAnswer=content,
            extractedClaimRefs=claims,
            citedRefs=(source.source_id,),
            sourceLedger=ledger,
        )
    )
    if not final_gate.ok:
        raise ValueError("research-first final projection did not pass")

    public_events = _public_events(
        ledger=ledger,
        final_gate=final_gate,
        source_hash=source_hash,
        unsupported_claims_omitted=1 if draft_gate is not None and not draft_gate.ok else 0,
    )
    metadata = _metadata(
        bot_id=bot_id,
        user_id=user_id,
        environment=environment,
        request_digest=request_ref,
        ledger=ledger,
        final_gate=final_gate,
        draft_gate=draft_gate,
        source_hash=source_hash,
    )
    return ResearchFirstSelectedResponse(
        content=content,
        public_events=public_events,
        metadata=metadata,
        final_gate_result=final_gate,
        draft_gate_result=draft_gate,
    )


def _draft_gate_if_unsupported_claim_requested(
    *,
    user_text: str,
    content: str,
    claims: tuple[ResearchClaimRef, ...],
    ledger: LocalResearchSourceLedger,
    turn_id: str,
) -> ResearchFinalGateResult | None:
    if "unsupported" not in user_text.casefold():
        return None
    draft_claims = (
        *claims,
        ResearchClaimRef(claimId="claim:unsupported-request", citedRefs=()),
    )
    return evaluate_research_final_gate(
        ResearchFinalGateRequest(
            contractId="research-first-selected-readonly",
            turnId=turn_id,
            mode="local_block_intent",
            candidateFinalAnswer=(
                content
                + " A requested additional factual claim is not projected without evidence."
            ),
            extractedClaimRefs=draft_claims,
            citedRefs=("src_1",),
            sourceLedger=ledger,
        )
    )


def _public_events(
    *,
    ledger: LocalResearchSourceLedger,
    final_gate: ResearchFinalGateResult,
    source_hash: str,
    unsupported_claims_omitted: int,
) -> tuple[PublicEvent, ...]:
    events: list[PublicEvent] = list(project_source_ledger_events(ledger))
    if final_gate.citation_audit_result is not None:
        events.extend(
            project_citation_audit_rule_events(
                final_gate.citation_audit_result,
                evidence_refs=(source_hash,),
            )
        )
    verifier_event = rule_check_event(
        rule_id="verifier:research-source-evidence",
        verdict="ok",
        detail="verifier status=pass",
    )
    verifier_event["evidenceRef"] = source_hash
    events.append(verifier_event)
    projection_event = rule_check_event(
        rule_id="final_projection:research-first",
        verdict="ok",
        detail=(
            "final projection status=passed "
            f"renderedClaims={len(final_gate.extracted_claim_refs)} "
            f"unsupportedClaimsOmitted={unsupported_claims_omitted}"
        ),
    )
    projection_event["evidenceRef"] = final_gate.final_answer_digest
    events.append(projection_event)
    return tuple(events)


def _metadata(
    *,
    bot_id: str,
    user_id: str,
    environment: str,
    request_digest: str,
    ledger: LocalResearchSourceLedger,
    final_gate: ResearchFinalGateResult,
    draft_gate: ResearchFinalGateResult | None,
    source_hash: str,
) -> dict[str, object]:
    blocked_reason_codes: tuple[str, ...] = ()
    if draft_gate is not None and not draft_gate.ok:
        blocked_reason_codes = draft_gate.reason_codes
    return {
        "schemaVersion": "openmagi.researchFirstSelectedCanary.v1",
        "enabled": True,
        "mode": "selected_readonly",
        "recipeId": RESEARCH_RECIPE_ID,
        "requestDigest": request_digest,
        "selectedScope": {
            "selectedBotDigest": _sha256_text(bot_id),
            "selectedOwnerUserIdDigest": _sha256_text(user_id),
            "environment": environment if _SAFE_ENV_RE.fullmatch(environment) else "redacted",
        },
        "sourceLedger": {
            "ledgerDigest": _sha256_json(
                [record.model_dump(by_alias=True, mode="json") for record in ledger.snapshot()]
            ),
            "sources": [
                {
                    "sourceId": record.source_id,
                    "sourceRef": f"ref:{record.source_id}",
                    "contentHash": record.content_hash,
                    "retrievedAt": record.inspected_at,
                    "inspected": record.inspected,
                    "kind": record.kind,
                }
                for record in ledger.snapshot()
            ],
        },
        "claimEvidence": [
            {
                "claimId": claim.claim_id,
                "evidenceRefs": list(claim.cited_refs),
                "claimDigest": _sha256_text(
                    "|".join((claim.claim_id, ",".join(claim.cited_refs)))
                ),
            }
            for claim in final_gate.extracted_claim_refs
        ],
        "unsupportedClaimHandling": {
            "status": "repaired" if blocked_reason_codes else "not_requested",
            "omittedClaimCount": 1 if blocked_reason_codes else 0,
            "blockedReasonCodes": list(blocked_reason_codes),
        },
        "finalGate": final_gate.public_projection(),
        "evidenceRefs": [source_hash, final_gate.final_answer_digest],
        "authority": {
            "workspaceMutationAllowed": False,
            "memoryWriteAllowed": False,
            "browserActive": False,
            "channelDeliveryAllowed": False,
            "schedulerMutationAllowed": False,
            "writeAccessAllowed": False,
            "modelCallAttempted": False,
            "liveProviderCalled": False,
        },
    }


def _payload_selects_research_recipe(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    runtime_context = payload.get("runtimeContext")
    if not isinstance(runtime_context, Mapping):
        return False
    selection = runtime_context.get("explicitRecipeSelection")
    if not isinstance(selection, Mapping):
        return False
    if selection.get("mode") not in {"this_turn", "session"}:
        return False
    if selection.get("allowAdditionalAutoRecipes") is not True:
        return False
    refs = selection.get("requiredRecipeRefs")
    if not isinstance(refs, Sequence) or isinstance(refs, str):
        return False
    return any(
        isinstance(ref, Mapping) and ref.get("recipeId") == RESEARCH_RECIPE_ID
        for ref in refs
    )


def _extract_last_user_text(payload: Mapping[str, object]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, Sequence) and not isinstance(content, str):
            chunks = [
                block.get("text")
                for block in content
                if isinstance(block, Mapping) and isinstance(block.get("text"), str)
            ]
            return "\n".join(chunks)
    return ""


def _json_safe(value: object) -> object:
    try:
        json.dumps(value, sort_keys=True)
        return value
    except (TypeError, ValueError):
        return str(value)


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in _TRUE_VALUES


__all__ = [
    "RESEARCH_FIRST_CANARY_ENABLED_ENV",
    "RESEARCH_FIRST_CANARY_KILL_SWITCH_ENV",
    "RESEARCH_RECIPE_ID",
    "ResearchFirstSelectedResponse",
    "build_research_first_selected_response",
    "research_first_selected_canary_active",
]
