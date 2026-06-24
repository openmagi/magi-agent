"""F-MUT2 — ``output_rewrite`` custom_rule kind.

Second mutator kind in the customize wizard's surface (after F-MUT1's
``prompt_injection``). Wires the AFTER_TOOL_USE HookBus ``replace`` action
shape as a constrained author surface: an operator picks a regex pattern +
replacement string, and at runtime the dispatcher's :class:`ToolResult`
output text is rewritten in-place BEFORE the model reads it.

v1 ships a single mode — ``redact`` — that maps to ``re.sub(pattern,
replacement, text)`` per matching rule, composed deterministically in stored
order. Two future modes (``summarize`` + ``replace``) are explicitly
rejected by the validator with a pointer to the v2 admin-tier flag so an
operator never persists a rule the runtime cannot honestly apply.

Author contract (validator below):

    {
      mode: "redact"                                  # v1 — only mode supported
      pattern: str                                    # <= 4000 chars, valid re.compile
      replacement: str                                # <= 4000 chars
      scope?: "match_only" | "full_output"            # default "match_only"
      isRegex?: bool                                  # default true; false → re.escape
      toolMatch?: {include?: [str], exclude?: [str]}  # optional per-tool filter
    }

v1 explicitly rejects ``mode == "summarize"`` and ``mode == "replace"`` (both
deferred to v2 with an admin-tier flag) and caps ``pattern`` + ``replacement``
at 4000 characters each to bound the rewrite cost.

Apply contract:

* :func:`apply_output_rewrite_to_tool_result` is PURE — it takes an inbound
  :class:`ToolResult`, the list of enabled rules, and the dispatched tool
  name, and returns a NEW :class:`ToolResult` with ``output`` rewritten (or
  the original instance unchanged when no rule applies). Fail-safe-original
  on any rule-level error: a malformed rule never breaks the turn, it is
  silently dropped from the projection. Mirrors
  :func:`magi_agent.hooks.replace_payloads.coerce_replace_payload`'s
  "fail-safe to original on any validation error" semantics.
* The helper is a no-op when the master flag
  ``MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED`` is OFF; the caller is expected to
  check that flag before invoking (the helper itself is a side-effect-free
  building block).
* ``scope == "match_only"`` and ``scope == "full_output"`` are equivalent in
  v1: :func:`re.sub` replaces matches only, and the entire output string is
  what we pass to it. The scope axis exists so v2 can add a "wrap the entire
  output" mode without re-shaping the persisted payload.
"""

from __future__ import annotations

import re
from typing import Any

from magi_agent.tools.result import ToolResult

# Hard cap on the pattern + replacement strings (per spec §F-MUT2) — bounds
# rewrite cost when an operator authors an unbounded value and prevents a
# pathological regex from eating the turn.
PATTERN_MAX = 4000
REPLACEMENT_MAX = 4000

_VALID_SCOPES = frozenset({"match_only", "full_output"})


def validate_output_rewrite_payload(
    payload: Any, fires_at: Any
) -> list[str]:
    """Validate an ``output_rewrite.payload`` shape; return error list.

    Empty list means valid (matches the convention used by
    :func:`magi_agent.customize.custom_rules.validate_custom_rule`). The
    ``fires_at`` parameter must equal ``"after_tool_use"`` — output rewrite
    has no honest target at any other lifecycle slot (no tool result text
    exists yet at before_tool_use; pre_final already passed by the time the
    final answer commits).

    Modes ``"summarize"`` and ``"replace"`` are explicitly rejected with a
    pointer to the v2 admin-tier flag deferral. ``pattern`` + ``replacement``
    are each capped at :data:`PATTERN_MAX` / :data:`REPLACEMENT_MAX` chars.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["output_rewrite.payload must be an object"]

    if fires_at != "after_tool_use":
        errors.append(
            "output_rewrite rules may only fire at 'after_tool_use'"
        )

    mode = payload.get("mode")
    if mode in {"summarize", "replace"}:
        errors.append(
            f"output_rewrite.payload.mode {mode!r} is deferred to v2 with an "
            "admin-tier flag"
        )
    elif mode != "redact":
        errors.append(
            "output_rewrite.payload.mode must be 'redact' (v1)"
        )

    pattern = payload.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        errors.append(
            "output_rewrite.payload.pattern is required (non-empty string)"
        )
    elif len(pattern) > PATTERN_MAX:
        errors.append(
            f"output_rewrite.payload.pattern exceeds the {PATTERN_MAX}-char cap"
        )
    else:
        # The runtime applier calls ``re.compile`` (literal mode escapes the
        # pattern first, regex mode compiles as-is). Validate the regex
        # branch up-front so a malformed pattern is caught at PUT time, not
        # at the next tool call. Literal-mode strings are always re-compile
        # safe after ``re.escape``, so no compile probe is needed there.
        is_regex = payload.get("isRegex", True)
        if is_regex is True:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(
                    f"output_rewrite.payload.pattern is not a valid regex: {exc}"
                )

    replacement = payload.get("replacement")
    if not isinstance(replacement, str):
        errors.append(
            "output_rewrite.payload.replacement is required (string)"
        )
    elif len(replacement) > REPLACEMENT_MAX:
        errors.append(
            f"output_rewrite.payload.replacement exceeds the "
            f"{REPLACEMENT_MAX}-char cap"
        )

    scope = payload.get("scope", "match_only")
    if scope not in _VALID_SCOPES:
        errors.append(
            "output_rewrite.payload.scope must be 'match_only' or 'full_output'"
        )

    if "isRegex" in payload and not isinstance(payload["isRegex"], bool):
        errors.append("output_rewrite.payload.isRegex must be a boolean")

    tool_match = payload.get("toolMatch")
    if tool_match is not None:
        if not isinstance(tool_match, dict):
            errors.append("output_rewrite.payload.toolMatch must be an object")
        else:
            for key in ("include", "exclude"):
                if key in tool_match:
                    value = tool_match[key]
                    if not isinstance(value, list) or not all(
                        isinstance(item, str) and item.strip() for item in value
                    ):
                        errors.append(
                            f"output_rewrite.payload.toolMatch.{key} must be a "
                            "list of non-empty strings"
                        )

    return errors


def _payload(rule: dict[str, Any]) -> dict[str, Any] | None:
    """Extract ``what.payload`` from a rule dict, or ``None`` on shape error."""
    what = rule.get("what")
    if not isinstance(what, dict):
        return None
    payload = what.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _tool_match_matches(
    payload: dict[str, Any], tool_name: str
) -> bool:
    """Return whether the optional ``toolMatch`` filter accepts ``tool_name``.

    Absent ``toolMatch`` fires for every tool. When ``toolMatch.include`` is
    a non-empty list, ``tool_name`` MUST appear in it. When
    ``toolMatch.exclude`` is a non-empty list, ``tool_name`` MUST NOT appear
    in it. Both filters compose (include AND not-exclude). Malformed
    filters fail-closed (rule is skipped).
    """
    tool_match = payload.get("toolMatch")
    if not isinstance(tool_match, dict):
        return True
    include = tool_match.get("include")
    if isinstance(include, list) and include:
        if tool_name not in include:
            return False
    exclude = tool_match.get("exclude")
    if isinstance(exclude, list) and exclude:
        if tool_name in exclude:
            return False
    return True


def _result_text_of(result: ToolResult) -> str | None:
    """Return the string text content of a :class:`ToolResult`, or ``None``.

    ``ToolResult`` does not have a single canonical "text" field — the
    payload may live in ``output`` (raw) or be projected separately into
    ``llm_output`` / ``transcript_output``. The output rewrite rule only
    rewrites string text the model would see (``output``); structured /
    non-string outputs are left untouched (no honest rewrite target).
    """
    if isinstance(result.output, str):
        return result.output
    return None


def _with_rewritten_output(result: ToolResult, new_text: str) -> ToolResult:
    """Return a new :class:`ToolResult` with ``output`` replaced.

    Preserves every other field via :meth:`pydantic.BaseModel.model_copy`
    so the dispatcher's other contract (status / llm_output /
    transcript_output / metadata / artifact_refs / etc.) survives the
    rewrite untouched.
    """
    return result.model_copy(update={"output": new_text})


def apply_output_rewrite_to_tool_result(
    result: ToolResult,
    rules: list[dict[str, Any]],
    tool_name: str,
) -> ToolResult:
    """Return ``result`` with every matching ``output_rewrite`` rule applied.

    Pure function — input ``result`` is NOT mutated; a new
    :class:`ToolResult` is returned ONLY when at least one rule actually
    rewrote the text. For each enabled ``output_rewrite`` rule with
    ``firesAt == "after_tool_use"`` whose ``toolMatch`` (if any) accepts
    ``tool_name``, the rule's ``re.sub(pattern, replacement, text)`` is
    applied to the current text. Rules compose deterministically in stored
    order (the next rule sees the previous rule's output).

    Returns the original ``result`` instance unchanged when:

    * the result has no string ``output`` (no honest rewrite target),
    * no rule matches the tool name,
    * every matching rule's pattern fails to compile (fail-safe-drop), or
    * the composed rewrite produced text identical to the original.

    Malformed rules are silently dropped so a buggy rule never wedges a tool
    call.
    """
    text = _result_text_of(result)
    if text is None:
        return result

    rewritten = text
    any_change = False
    for rule in rules:
        try:
            if not rule.get("enabled", False):
                continue
            if rule.get("firesAt") != "after_tool_use":
                continue
            what = rule.get("what")
            if not isinstance(what, dict) or what.get("kind") != "output_rewrite":
                continue
            payload = _payload(rule)
            if payload is None:
                continue
            if payload.get("mode") != "redact":
                continue
            pattern = payload.get("pattern")
            replacement = payload.get("replacement")
            if not isinstance(pattern, str) or not pattern:
                continue
            if not isinstance(replacement, str):
                continue
            if not _tool_match_matches(payload, tool_name):
                continue
            is_regex = payload.get("isRegex", True)
            compiled_pattern = (
                pattern if is_regex is True else re.escape(pattern)
            )
            try:
                new_text = re.sub(compiled_pattern, replacement, rewritten)
            except re.error:
                continue
            if new_text != rewritten:
                rewritten = new_text
                any_change = True
        except Exception:  # noqa: BLE001 — fail-safe per module docstring
            continue

    if not any_change:
        return result
    return _with_rewritten_output(result, rewritten)


__all__ = [
    "PATTERN_MAX",
    "REPLACEMENT_MAX",
    "apply_output_rewrite_to_tool_result",
    "validate_output_rewrite_payload",
]
