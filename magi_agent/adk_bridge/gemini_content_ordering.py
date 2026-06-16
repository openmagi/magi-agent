"""Gemini content-ordering repair for the ADK before_model hook.

Why
---
Across multi-tool turns the ADK ``Runner`` can assemble an ``LlmRequest`` whose
``contents`` violate Gemini's structural rule:

    "Please ensure that function call turn comes immediately after a user turn
     or after a function response turn." (HTTP 400 INVALID_ARGUMENT)

This happens when the model returns text *and* a ``function_call`` that ADK
records as two consecutive ``model`` contents (ADK logs
``Warning: there are non-text parts in the response: ['function_call']``), or
when a manual tool-followup is appended such that a ``function_call`` model turn
ends up immediately preceded by another ``model`` turn. Gemini rejects the whole
request, the runner raises ``ClientError 400`` and the turn dies mid-stream as a
generic ``runner_error`` — the user sees the reasoning text cut off mid-sentence.

Fix
---
Normalise ``contents`` so roles strictly alternate by **merging adjacent
contents that share a role** (concatenating their parts in order). After this,
every ``model`` content (the only role that can carry a ``function_call``) is
immediately preceded by a non-``model`` content — i.e. a user / function_response
turn — which satisfies the rule. Merging never drops or reorders parts, and a
``model`` content carrying both ``text`` and ``function_call`` parts is exactly
what the model originally produced, so it stays valid.

This module is intentionally dependency-light and duck-typed over the
``google.genai`` ``Content``/``Part`` shape so it is unit-testable without ADK or
a live provider. ``repair_gemini_content_ordering`` is a pure function; the
``GeminiContentOrderingRepairControl`` wires it into the ADK control-plane's
``on_before_model`` fan-out.
"""

from __future__ import annotations

from typing import Any

GEMINI_CONTENT_ORDER_REPAIR_CONTROL_NAME = "magi_gemini_content_order_repair"


def _role(content: Any) -> Any:
    return getattr(content, "role", None)


def _parts(content: Any) -> list[Any]:
    parts = getattr(content, "parts", None)
    return list(parts) if parts else []


def repair_gemini_content_ordering(contents: Any) -> list[Any] | None:
    """Return contents with adjacent same-role turns merged, or ``None`` if no
    change is needed.

    Merges in place at the part level by mutating the surviving content's
    ``parts`` list, so the returned objects are the original ``Content``
    instances (ADK/genai keep object identity where possible). Returns ``None``
    when the input is not a non-empty sequence or already alternates, so callers
    can cheaply skip the no-op case.
    """
    if not isinstance(contents, (list, tuple)) or len(contents) < 2:
        return None

    merged: list[Any] = []
    changed = False
    for content in contents:
        if content is None:
            continue
        if merged and _role(merged[-1]) is not None and _role(merged[-1]) == _role(content):
            prev = merged[-1]
            prev_parts = getattr(prev, "parts", None)
            incoming = _parts(content)
            if isinstance(prev_parts, list):
                prev_parts.extend(incoming)
            else:
                try:
                    prev.parts = _parts(prev) + incoming
                except Exception:
                    merged.append(content)
                    continue
            changed = True
        else:
            merged.append(content)

    if not changed:
        return None
    return merged


def _is_function_response_only(content: Any) -> bool:
    parts = getattr(content, "parts", None) or []
    if not parts:
        return False
    return all(getattr(p, "function_response", None) is not None for p in parts)


def ensure_gemini_user_opener(contents: Any, user_content_factory: Any) -> list | None:
    """Make ``contents`` start with a valid Gemini opener, or return ``None``.

    Gemini 400s ("function call turn must come immediately after a user turn or
    function response turn") when context compaction trims the conversation head
    and leaves ``contents`` starting with a model ``function_call`` turn (the
    original user prompt was dropped) or a dangling ``function_response`` turn.

    Repair: drop leading orphaned ``function_response`` turns, then prepend a
    synthetic user turn (built by ``user_content_factory()``) when the head is a
    model turn. Returns a new list if anything changed, else ``None``.
    """
    if not isinstance(contents, (list, tuple)) or not contents:
        return None
    work = list(contents)
    changed = False
    while work and _is_function_response_only(work[0]):
        work.pop(0)
        changed = True
    if work and _role(work[0]) == "model":
        work.insert(0, user_content_factory())
        changed = True
    return work if changed else None


def apply_gemini_content_ordering_repair(
    llm_request: Any,
    user_content_factory: Any = None,
) -> bool:
    """Repair ``llm_request.contents`` in place. Returns True if it changed.

    Two repairs: (1) merge adjacent same-role turns so roles alternate; (2) when
    ``user_content_factory`` is given, fix a compaction-orphaned head (drop
    leading dangling function_response turns; prepend a synthetic user turn when
    the head is a model turn) so a leading ``function_call`` turn is preceded by
    a user turn. The factory is injected by the control so this stays
    unit-testable without importing google.genai.
    """
    contents = getattr(llm_request, "contents", None)

    merged = repair_gemini_content_ordering(contents)
    changed = merged is not None
    current = merged if changed else contents

    if user_content_factory is not None:
        opened = ensure_gemini_user_opener(current, user_content_factory)
        if opened is not None:
            current = opened
            changed = True

    if changed:
        llm_request.contents = current
    return changed
