"""Build a durable evidence-ledger record for a turn's human-facing bookends.

A run-share page needs the *top* of a run that the per-tool evidence stream does
not carry: the goal the human gave, the one-line result, the model used, token
usage, and the final status. This module turns those into ONE record dict of the
shape :func:`magi_agent.evidence.ledger_store.write_evidence_records` accepts
(``{toolName, status, record}``), so it lands on the SAME durable
``<dir>/<session>.jsonl`` the tool evidence already uses. No second writer.

Safety: the payload is **allowlist fail-closed**. It is constructed key-by-key
from typed scalars, so an unrecognized structure can never reach a shared link.
Free-text ``goal``/``result`` are redacted (the ledger's public-summary redactor)
and truncated before inclusion. Numeric/identity fields are emitted only when
present; absent values are OMITTED, never serialized as ``null``.

Pure and side-effect free: the caller decides whether/where to persist.
"""
from __future__ import annotations

from magi_agent.evidence.ledger import (
    _redact_public_summary_text,
    _truncate_public_strings,
)

__all__ = [
    "RUN_BOOKEND_SCHEMA_VERSION",
    "RUN_BOOKEND_TOOL_NAME",
    "build_run_bookend_record",
]

RUN_BOOKEND_SCHEMA_VERSION = "openmagi.runBookend.v1"
# Surfaces as the wrapper ``toolName`` so readers/UIs can discriminate the
# bookend line from per-tool evidence without parsing the payload.
RUN_BOOKEND_TOOL_NAME = "RunBookend"

# Statuses we are willing to publish verbatim; anything else is coerced to
# "unknown" so a stray object/enum can never widen the public surface. Mirrors
# ``magi_agent.cli.contracts.Terminal`` plus "partial"/"unknown".
_KNOWN_STATUSES = frozenset(
    {"ok", "completed", "aborted", "error", "max_turns", "partial", "unknown"}
)

# Backtracking guard: the public-summary redactor is super-linear in input
# length (a latent issue tracked for the dedicated redaction phase). We bound
# the redaction window before running it. The bound is generous relative to the
# published cap (``_truncate_public_strings`` keeps ~200 chars) so a secret that
# appears inside the published window still has its closing delimiter inside the
# redaction window and gets scrubbed. ~600 chars is ~60ms worst case.
_REDACT_INPUT_CAP = 600


def _coerce_str(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def _safe_text(value: str) -> str:
    """Redact, then truncate, a free-text string for a public share surface.

    Redaction runs BEFORE truncation so a secret near the published-cap boundary
    cannot survive by having its closing delimiter cut off (which would defeat
    the quoted ``key="..."`` redaction patterns). The input is first bounded to
    ``_REDACT_INPUT_CAP`` purely to cap the redactor's super-linear cost; that
    bound has wide headroom over the published cap so the published window is
    always fully redacted.
    """
    bounded = _coerce_str(value)[:_REDACT_INPUT_CAP]
    redacted = _redact_public_summary_text(bounded)
    final = _truncate_public_strings(redacted)
    return _coerce_str(final)


def _coerce_status(status: object) -> str:
    text = status if isinstance(status, str) else str(status)
    return text if text in _KNOWN_STATUSES else "unknown"


def _non_negative_int(value: object) -> int | None:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def build_run_bookend_record(
    *,
    session_id: str,
    turn_id: str,
    goal: str,
    result: str | None,
    status: object,
    model: str | None,
    provider: str | None,
    input_tokens: object,
    output_tokens: object,
    cost_usd: object,
) -> dict:
    """Return a ``{toolName, status, record}`` dict for one run's bookends.

    Every value is allowlisted by construction. ``goal``/``result`` are redacted
    and truncated. ``model``/``usage``/``costUsd`` are emitted only when present.
    """
    status_text = _coerce_status(status)

    payload: dict[str, object] = {
        "schemaVersion": RUN_BOOKEND_SCHEMA_VERSION,
        "sessionId": session_id,
        "turnId": turn_id,
        "status": status_text,
        "goal": _safe_text(goal if isinstance(goal, str) else str(goal)),
    }

    if isinstance(result, str) and result.strip():
        payload["result"] = _safe_text(result)

    if isinstance(model, str) and model.strip():
        model_block: dict[str, str] = {"label": model}
        if isinstance(provider, str) and provider.strip():
            model_block["provider"] = provider
        payload["model"] = model_block

    usage: dict[str, int] = {}
    in_tokens = _non_negative_int(input_tokens)
    out_tokens = _non_negative_int(output_tokens)
    if in_tokens is not None:
        usage["inputTokens"] = in_tokens
    if out_tokens is not None:
        usage["outputTokens"] = out_tokens
    if usage:
        payload["usage"] = usage

    if isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool):
        if cost_usd >= 0:
            payload["costUsd"] = float(cost_usd)

    return {
        "toolName": RUN_BOOKEND_TOOL_NAME,
        "status": status_text,
        "record": payload,
    }
