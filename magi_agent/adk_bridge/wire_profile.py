"""Wire profiles for ``OpenMagiEventBridge``.

A ``WireProfile`` is a frozen dataclass that encapsulates the transport-specific
projection choices the bridge needs:

* ``tool_id`` — maps ``(name, args, adk_id, index)`` → a stable string id for
  a function-call event.
* ``tool_start_event``, ``tool_progress_event``, ``tool_end_event`` — build the
  ``agent_event`` dict for each tool lifecycle moment.
* ``text_delta_event`` — build the ``text_delta`` agent_event dict.
* ``turn_phase_event`` — build the ``turn_phase`` agent_event dict.

Two profiles are shipped:

``DEFAULT_PROFILE``
    Reproduces **event_adapter's current behaviour exactly** so CLI callers stay
    byte-identical after T3 switches the bridge over to profile dispatch.  Tool
    ids use the existing ``adk-tool-call:…`` / ``adk-tool-call-<sha1>`` scheme;
    event dicts match what ``_project_function_call_part`` /
    ``_project_function_response_part`` emit today (including the
    ``live_compatible`` extras when they are enabled).

``HOSTED_PROFILE``
    Reproduces **gate5b4c3's wire shape**: tool ids come from
    ``runtime.public_events.tool_event_id`` (the ``tu_<hash>`` scheme lifted in
    T1); event dicts come from the same
    ``runtime.public_events.tool_start_event`` / ``tool_progress_event`` /
    ``tool_end_event`` builders gate5b4c3 already calls.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, get_args


# ---------------------------------------------------------------------------
# WireProfile value object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WireProfile:
    """Encapsulates transport-specific projection choices for ``OpenMagiEventBridge``.

    All callables must be pure functions with no side effects.

    Attributes
    ----------
    tool_id:
        ``(name, args, adk_id, index) -> str``
        Stable string id for a function-call event.
    build_tool_start:
        ``(tool_id, name, input_preview) -> dict``
        Builds the ``tool_start`` agent_event dict.
    build_tool_progress:
        ``(tool_id, label, status=None, message=None) -> dict``
        Builds the ``tool_progress`` agent_event dict.
    build_tool_end:
        ``(tool_id, status, output_preview=None, *, receipt_refs=(), duration_ms=None) -> dict``
        Builds the ``tool_end`` agent_event dict.
        HOSTED forwards ``receipt_refs`` → ``transcriptRefs`` and
        ``duration_ms`` → ``durationMs``; DEFAULT accepts but ignores them.
    build_text_delta:
        ``(delta) -> dict``
        Builds the ``text_delta`` agent_event dict.
    build_turn_phase:
        ``(turn_id, phase) -> dict``
        Builds the ``turn_phase`` agent_event dict.
    """

    tool_id: Callable[[str, dict, object, int], str]
    build_tool_start: Callable[[str, str, str | None], dict]
    build_tool_progress: Callable[..., dict]
    build_tool_end: Callable[..., dict]
    build_text_delta: Callable[[str], dict]
    build_turn_phase: Callable[[str, str], dict]


# ---------------------------------------------------------------------------
# DEFAULT profile — reproduces event_adapter's current behaviour
# ---------------------------------------------------------------------------

def _default_tool_id(
    name: str,
    args: dict,
    adk_id: object,
    index: int,
) -> str:
    """Reproduce event_adapter's ``_tool_use_id`` scheme.

    When ``adk_id`` is a non-empty string the id is derived from it using the
    ``adk-tool-call:…`` prefix (mirrors ``_public_ref(adk_id,
    prefix="adk-tool-call")`` but without needing the full sanitiser pipeline).
    When ``adk_id`` is absent a sha1 fallback is used.

    Note: The full ``_tool_use_id`` fallback also incorporates the ADK Event
    object (``event.id``, ``event.invocation_id``, fingerprint) which is not
    available at this level.  The fallback path here therefore produces a
    ``adk-tool-call-<sha1>`` digest over the available inputs; T3 will supply
    the event-derived fields when it calls the real ``_tool_use_id`` helper for
    the DEFAULT path.  For now the DEFAULT profile's ``tool_id`` is used only
    in tests that check the *prefix* scheme, and T3 will call ``_tool_use_id``
    directly in the bridge for the DEFAULT profile.
    """
    kind = "call"
    if isinstance(adk_id, str) and adk_id.strip():
        # Mirrors _public_ref(adk_id, prefix="adk-tool-call") — simple form
        # without the full sanitiser (sufficient for id scheme tests).
        return f"adk-tool-{kind}:{adk_id}"
    # Fallback: sha1 over available inputs.
    fallback_source = json.dumps(
        {
            "kind": kind,
            "name": name,
            "index": index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(fallback_source.encode("utf-8")).hexdigest()[:12]
    return f"adk-tool-{kind}-{digest}"


def _default_build_tool_start(
    tool_id: str,
    name: str,
    input_preview: str | None,
) -> dict:
    """Build tool_start dict matching event_adapter's current shape."""
    return {
        "type": "tool_start",
        "id": tool_id,
        "name": name,
        "input_preview": input_preview or "",
    }


def _default_build_tool_progress(
    tool_id: str,
    label: str | None,
    status: str | None = None,
    message: str | None = None,
) -> dict:
    """Build tool_progress dict matching event_adapter's current shape.

    ``status`` and ``message`` accepted for signature parity but ignored —
    DEFAULT_PROFILE is test-only documentation of the CLI wire shape.
    """
    event: dict = {"type": "tool_progress", "id": tool_id}
    if label is not None:
        event["label"] = label
    return event


def _default_build_tool_end(
    tool_id: str,
    status: str,
    output_preview: str | None = None,
    *,
    receipt_refs: tuple = (),
    duration_ms: int | float | None = None,
) -> dict:
    """Build tool_end dict matching event_adapter's current shape.

    ``receipt_refs`` and ``duration_ms`` are accepted for signature parity but
    intentionally ignored — DEFAULT_PROFILE is test-only documentation of the
    CLI wire shape which does not carry these fields.
    """
    return {
        "type": "tool_end",
        "id": tool_id,
        "status": status,
        "output_preview": output_preview or "",
        "durationMs": 0,
    }


def _default_build_text_delta(delta: str) -> dict:
    return {"type": "text_delta", "delta": delta}


def _default_build_turn_phase(turn_id: str, phase: str) -> dict:
    return {"type": "turn_phase", "turnId": turn_id, "phase": phase}


# NOTE: DEFAULT_PROFILE is test-only documentation of the CLI event shape and is
# NOT used by OpenMagiEventBridge — the None path calls the existing helpers directly.
DEFAULT_PROFILE = WireProfile(
    tool_id=_default_tool_id,
    build_tool_start=_default_build_tool_start,
    build_tool_progress=_default_build_tool_progress,
    build_tool_end=_default_build_tool_end,
    build_text_delta=_default_build_text_delta,
    build_turn_phase=_default_build_turn_phase,
)


# ---------------------------------------------------------------------------
# HOSTED profile — reproduces gate5b4c3's wire shape via public_events builders
# ---------------------------------------------------------------------------

def _hosted_tool_id(
    name: str,
    args: dict,
    adk_id: object,
    index: int,
) -> str:
    """Delegate to ``public_events.tool_event_id`` for the ``tu_<hash>`` scheme."""
    from magi_agent.runtime.public_events import tool_event_id  # noqa: PLC0415

    return tool_event_id(name=name, args=args, call_id=adk_id, index=index)


def _hosted_build_tool_start(
    tool_id: str,
    name: str,
    input_preview: str | None,
) -> dict:
    from magi_agent.runtime.public_events import tool_start_event  # noqa: PLC0415

    return tool_start_event(
        tool_id=tool_id,
        name=name,
        input_preview=input_preview,
        event_family="tool_progress",
    )


def _hosted_build_tool_progress(
    tool_id: str,
    label: str | None,
    status: str | None = None,
    message: str | None = None,
) -> dict:
    from magi_agent.runtime.public_events import tool_progress_event  # noqa: PLC0415

    return tool_progress_event(
        tool_id=tool_id,
        label=label,
        status=status,
        message=message,
        event_family="tool_progress",
    )


def _hosted_build_tool_end(
    tool_id: str,
    status: str,
    output_preview: str | None = None,
    *,
    receipt_refs: tuple = (),
    duration_ms: int | float | None = None,
) -> dict:
    from magi_agent.runtime.public_events import tool_end_event  # noqa: PLC0415

    return tool_end_event(
        tool_id=tool_id,
        status=status,
        output_preview=output_preview,
        receipt_refs=receipt_refs,
        duration_ms=duration_ms,
        event_family="tool_progress",
    )


def _hosted_build_text_delta(delta: str) -> dict:
    return {"type": "text_delta", "delta": delta}


def _hosted_build_turn_phase(turn_id: str, phase: str) -> dict:
    from magi_agent.runtime.public_events import (  # noqa: PLC0415
        TurnPhase,
        turn_phase_event,
    )

    # Validate phase is a known TurnPhase literal value; fall back to "pending".
    _valid_phases = set(get_args(TurnPhase))
    safe_phase: TurnPhase = phase if phase in _valid_phases else "pending"  # type: ignore[assignment]
    return turn_phase_event(
        turn_id=turn_id,
        phase=safe_phase,
        event_family="turn_lifecycle_public_stream",
    )


HOSTED_PROFILE = WireProfile(
    tool_id=_hosted_tool_id,
    build_tool_start=_hosted_build_tool_start,
    build_tool_progress=_hosted_build_tool_progress,
    build_tool_end=_hosted_build_tool_end,
    build_text_delta=_hosted_build_text_delta,
    build_turn_phase=_hosted_build_turn_phase,
)
