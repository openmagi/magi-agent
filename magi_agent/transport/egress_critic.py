"""Egress critic gate and live evidence projection for the chat serving path.

Pure move out of ``magi_agent/transport/chat.py`` (08-PR1). Projects the live
turn evidence (gate5b toolhost ReadLedger + tool receipts) into the PR1
``SessionEvidenceView`` and runs the fail-open egress critic check against the
draft response when ``MAGI_EGRESS_GATE_ENABLED`` is ON. Behavior is unchanged;
``transport.chat`` re-exports these names for compatibility.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os

from magi_agent.gates.gate1a_readonly_tools import Gate1AReadOnlyToolBundle
from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolBundle
from magi_agent.introspection.egress_gate import EgressVerifierStatus
from magi_agent.runtime.user_visible_model_routing import _SAFE_LABEL_RE
from magi_agent.transport.generation_request import _extract_last_user_text

def _build_egress_evidence_view(
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
):
    """Project the LIVE turn evidence into a PR1 ``SessionEvidenceView``.

    Reuses the PR1 view models. The only real per-turn evidence reachable at
    egress is on the gate5b full toolhost:
      - ``host.read_ledger`` (a real ``ReadLedger``) -> files_read.
      - ``host.counter.receipts`` (tool receipts)    -> tool_calls.
    There is no live ``EvidenceLedger`` at this seam (the toolhost records reads
    in the ReadLedger and tool outcomes as receipts, not as EvidenceLedger
    entries), so files_read are projected directly from the ReadLedger exactly
    as PR2's ``_empty_view_with_optional_reads`` does, and tool_calls from the
    receipts. Phases/verdicts stay empty (no live producer). Pure / read-only;
    never raises.
    """
    from magi_agent.introspection.mapping import tool_call_from_gate5b_receipt
    from magi_agent.introspection.projection import (
        FileReadView,
        SessionEvidenceView,
        SessionScopeView,
    )

    host = getattr(gate1a_bundle, "host", None)
    read_ledger = getattr(host, "read_ledger", None)
    receipts = tuple(getattr(getattr(host, "counter", None), "receipts", ()) or ())

    files_read: list[FileReadView] = []
    turns: list[str] = []
    # No real session id is threaded to this seam (the builder only receives the
    # tool bundle, not the chat payload). PR2's projection filters the ReadLedger
    # by a known session id; here we derive it from the FIRST read entry and skip
    # any later entries from a different session, keeping the view session-scoped
    # consistent with PR2. If there are no reads the placeholder is retained.
    session_id = "live-egress-session"
    if read_ledger is not None:
        for entry in read_ledger.iter_entries():
            if files_read:
                # Session pinned to the first entry — skip cross-session entries.
                if entry.session_id != session_id:
                    continue
            else:
                session_id = entry.session_id
            turns.append(entry.turn_id)
            files_read.append(
                FileReadView(
                    path=entry.path,
                    sha256=entry.digest,
                    turnId=entry.turn_id,
                    bytes=entry.size_bytes,
                )
            )

    # TWO-PRODUCER REALITY: tool_calls here are sourced from gate5b
    # ``host.counter.receipts`` (the egress-time PUSH producer), whereas the
    # ``InspectSelfEvidence`` tool sources tool_calls from EvidenceLedger
    # records (the mid-turn PULL producer). These two producers live at
    # genuinely different runtime seams — there is no EvidenceLedger reachable
    # here and no gate5b receipt reachable in ToolContext — so the SOURCES
    # cannot be unified. Instead both seams now delegate to the shared
    # normalization in ``introspection/mapping.py``, which guarantees an
    # identical ``ToolCallView`` shape + canonical status vocabulary so the two
    # producers report comparable outcomes (e.g. a success -> "ok" from either).
    # The receipts carry no per-entry session/turn id at this seam, so the pinned
    # session's placeholder turn id is used for all of them.
    tool_calls = tuple(
        tool_call_from_gate5b_receipt(receipt, "live-egress-turn")
        for receipt in receipts
    )
    return SessionEvidenceView(
        scope=SessionScopeView(
            sessionId=session_id,
            turnsCovered=tuple(dict.fromkeys(turns)),
        ),
        filesRead=tuple(files_read),
        toolCalls=tool_calls,
        phases=(),
        verdicts=(),
    )


async def _maybe_run_egress_critic_gate(
    *,
    payload: object,
    draft_text: str,
    gate1a_bundle: Gate1AReadOnlyToolBundle | Gate5BFullToolBundle,
) -> EgressVerifierStatus | None:
    """Run the egress critic gate when the flag is ON. Fail-open; never raises.

    Returns the ``verifier_evidence_status`` value (or ``None`` for no signal /
    not fact-critical / fail-open error). When the flag is OFF this is never
    called, so the egress path is byte-identical to before.
    """
    try:
        from magi_agent.introspection.egress_gate import run_egress_critic_check

        user_query = (
            _extract_last_user_text(payload) if isinstance(payload, Mapping) else ""
        )
        view = _build_egress_evidence_view(gate1a_bundle)
        model_factory = _egress_critic_model_factory(payload)

        evidence_records: list[dict[str, object]] = []
        result = await run_egress_critic_check(
            draft_text=draft_text or "",
            user_query=user_query or "",
            view=view,
            model_factory=model_factory,
            evidence_sink=evidence_records.append,
        )
        for record in evidence_records:
            _log_egress_critic_evidence(record)
        return result.status
    except Exception:  # noqa: BLE001 — egress gate must NEVER break the response
        return None


# Haiku-class fast-model override for the egress critic / fact-critical
# classifier (analogous to ``MAGI_SMART_APPROVE_MODEL`` for SmartApprove). When
# unset the critic uses the runtime's configured provider model.
_ENV_EGRESS_CRITIC_MODEL = "MAGI_EGRESS_CRITIC_MODEL"


def _egress_critic_model_factory(payload: object) -> Callable[[], object] | None:
    """Resolve the critic model factory.

    Resolution order:
      1. Test injection — ``payload["_egressCriticModelFactory"]`` (a private,
         test-only key ignored by the rest of the pipeline) ALWAYS wins so tests
         stay hermetic and never touch a real provider.
      2. Production — build a real Haiku-class model from the runtime's provider
         configuration, reusing the SAME mechanism SmartApprove's
         ``ReadOnlyClassifier`` uses (``resolve_provider_config`` ->
         ``_build_litellm_for_config``). The fast model is overridable via the
         ``MAGI_EGRESS_CRITIC_MODEL`` env var.

    Fail-open is sacrosanct: if no provider config / key can be resolved, or the
    litellm dependency is unavailable, this returns ``None`` and the gate stays
    dormant (status ``None``) — never erroring into the response. Enabling the
    flag without a configured model is therefore always safe.
    """
    if isinstance(payload, Mapping):
        factory = payload.get("_egressCriticModelFactory")
        if callable(factory):
            return factory  # type: ignore[return-value]
    return _production_egress_critic_model_factory()


# Sensible Haiku-class fallback used ONLY if the resolved provider config cannot
# yield its own default model string. Keeps the egress critic explicitly resolved
# rather than ever inheriting SmartApprove's pinned env model.
_EGRESS_CRITIC_DEFAULT_MODEL = "anthropic/claude-haiku-4-5"


def _production_egress_critic_model_factory() -> Callable[[], object] | None:
    """Build a provider-backed critic model factory, or ``None`` (fail open).

    Reuses the exact resolution path of the SmartApprove read-only classifier:
    ``resolve_provider_config()`` discovers the active provider/key from the same
    ``~/.magi/config.toml`` + env sources the runner uses, and
    ``_build_litellm_for_config()`` constructs the ADK ``LiteLlm`` model.

    Model resolution order (resolved EXPLICITLY here so the egress critic never
    silently inherits ``MAGI_SMART_APPROVE_MODEL``):
      1. ``MAGI_EGRESS_CRITIC_MODEL`` env var (Haiku-class fast override), else
      2. the resolved provider config's OWN default model
         (``ProviderConfig.litellm_model``), else
      3. a fixed sensible Haiku-class default (``_EGRESS_CRITIC_DEFAULT_MODEL``).

    A concrete ``model_override`` string is ALWAYS passed into
    ``_build_litellm_for_config`` so SmartApprove's env override is never
    consulted for the egress critic (no cross-coupling). SmartApprove's own
    resolution is unchanged.
    """
    try:
        from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

        provider_config = resolve_provider_config()
    except Exception:  # noqa: BLE001 — fail open (no provider config -> dormant)
        return None

    if provider_config is None:
        # No provider / key configured -> gate stays dormant (fail open).
        return None

    # Explicit resolution: egress env -> provider default -> fixed Haiku default.
    model_override = os.environ.get(_ENV_EGRESS_CRITIC_MODEL, "").strip()
    if not model_override:
        provider_default = getattr(provider_config, "litellm_model", None)
        model_override = (provider_default or "").strip() or _EGRESS_CRITIC_DEFAULT_MODEL

    def _factory() -> object:
        from magi_agent.cli.readonly_classifier import (  # noqa: PLC0415
            _build_litellm_for_config,
        )

        # Pass a concrete model string so the SmartApprove env override
        # (MAGI_SMART_APPROVE_MODEL) is NEVER consulted for the egress critic.
        return _build_litellm_for_config(provider_config, model_override=model_override)

    return _factory


def _log_egress_critic_evidence(record: Mapping[str, object]) -> None:
    """Best-effort structured log of one egress-critic evidence record."""
    try:
        import logging  # noqa: PLC0415

        logging.getLogger("magi_agent.introspection.egress_gate").info(
            "egress_critic_evidence %s",
            json.dumps(
                _safe_egress_critic_evidence_log_record(record),
                ensure_ascii=False,
                default=str,
            ),
        )
    except Exception:  # noqa: BLE001
        pass


def _safe_egress_critic_evidence_log_record(
    record: Mapping[str, object],
) -> dict[str, object]:
    safe_record = dict(record)
    reason = safe_record.get("reason")
    if isinstance(reason, str) and reason and not _SAFE_LABEL_RE.match(reason):
        try:
            from magi_agent.introspection.reason_safety import (  # noqa: PLC0415
                safe_model_reason,
            )

            safe_reason = safe_model_reason(reason, label="egress_reason")
        except Exception:  # noqa: BLE001
            safe_record["reason"] = "egress_reason"
            return safe_record
        safe_record["reason"] = safe_reason.label
        if safe_reason.digest is not None:
            safe_record.setdefault("reason_digest", safe_reason.digest)
        if safe_reason.preview is not None:
            safe_record.setdefault("reason_preview", safe_reason.preview)
    return safe_record
