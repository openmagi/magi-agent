"""Runtime-fields derivation for the wizard's variable chip picker (F-UX2 / F8).

Pure-function module: derives the set of runtime variables the operator can
reference, per (lifecycle, condition, tool?) tuple. Mirrors the
control-plane reference pattern Kevin shared in the F-UX series design doc:
the chip menu rendered above a regex / criterion / SHACL text input is sourced
from the SAME signature the runtime gate actually receives, so the operator
never guesses a field name the runtime cannot honor.

The module is intentionally pure:
  * no I/O (no env reads beyond what callers pass in via the tool registry),
  * no flag-bool checks (the caller decides whether to expose),
  * no LLM calls,
  * deterministic + side-effect-free (two calls with the same inputs return
    identical lists),
  * fail-open via empty list, never raises.

Derivation sources (discovery + per_tuple_variables map from the design doc):
  * Context-free fields (session_id, turn_id, tool_name, tool_use_id, ...)
    are HARD-CODED per lifecycle and listed in :data:`_LIFECYCLE_BASE_FIELDS`.
  * ``tool_input.*`` expansion reads ``ToolManifest.input_schema['properties']``
    via ``ToolRegistry.resolve(name)`` so each tool surfaces its real argument
    schema. Without a tool name we surface the generic ``tool_input.*`` marker
    plus the canonical alias hints from ``tool_perm._URL_ARG_KEYS`` /
    ``_PATH_ARG_KEYS`` (per the design doc's URL/path matcher table).
  * ``evidence:<type>.fields.*`` reads F2's ``_BUILTIN_FIELD_HINTS`` exposed
    via :func:`magi_agent.customize.shacl_compiler.available_fields`.

The endpoint is read-only and the caller (transport/customize.py) routes any
exception to a fail-open empty payload.
"""

from __future__ import annotations

from typing import Any

from magi_agent.customize.tool_perm import _PATH_ARG_KEYS, _URL_ARG_KEYS


# Field record shape: {name, type, description}. Mirrors the control-plane
# chip vocabulary so the dashboard can render uniformly.
RuntimeField = dict[str, str]


# ---------------------------------------------------------------------------
# Per-lifecycle context-free fields (hard-coded per the discovery's
# ``hardcoded_context_vars`` set).
#
# These are the variables the runtime threads through the gate signature
# regardless of which tool / condition the rule targets. ``tool_name`` and
# ``tool_use_id`` are scoped to tool-bearing lifecycles only.
# ---------------------------------------------------------------------------
_LIFECYCLE_BASE_FIELDS: dict[str, list[RuntimeField]] = {
    "before_tool_use": [
        {
            "name": "session_id",
            "type": "string",
            "description": "Stable session identifier for the governed turn.",
        },
        {
            "name": "turn_id",
            "type": "string",
            "description": "Stable turn identifier (ADK invocation_id at the boundary).",
        },
        {
            "name": "tool_name",
            "type": "string",
            "description": "Registered tool name; matches ToolManifest.name.",
        },
        {
            "name": "tool_use_id",
            "type": "string",
            "description": "Per-invocation tool-call id (wire form: tu_<sha>).",
        },
    ],
    "after_tool_use": [
        {
            "name": "session_id",
            "type": "string",
            "description": "Stable session identifier for the governed turn.",
        },
        {
            "name": "turn_id",
            "type": "string",
            "description": "Stable turn identifier (ADK invocation_id at the boundary).",
        },
        {
            "name": "tool_name",
            "type": "string",
            "description": "Registered tool name; matches ToolManifest.name.",
        },
        {
            "name": "tool_use_id",
            "type": "string",
            "description": "Per-invocation tool-call id (wire form: tu_<sha>).",
        },
        {
            "name": "tool_result_text",
            "type": "string",
            "description": "Flattened textual rendering of ToolResult.output (used for contentMatch).",
        },
        {
            "name": "tool_result_truncated",
            "type": "bool",
            "description": "True when the output_chars budget caused truncation (cap_text return).",
        },
    ],
    "pre_final": [
        {
            "name": "session_id",
            "type": "string",
            "description": "Stable session identifier for the governed turn.",
        },
        {
            "name": "turn_id",
            "type": "string",
            "description": "Stable turn identifier (ADK invocation_id at the boundary).",
        },
    ],
    "on_user_prompt_submit": [
        {
            "name": "user_prompt_text",
            "type": "string",
            "description": "Inbound user prompt for the turn (passed as draft_text to the criterion judge).",
        },
        {
            "name": "session_id",
            "type": "string",
            "description": "Stable session identifier for the governed turn.",
        },
        {
            "name": "turn_id",
            "type": "string",
            "description": "Stable turn identifier (ADK invocation_id at the boundary).",
        },
    ],
    "on_subagent_stop": [
        {
            "name": "child_final_text",
            "type": "string",
            "description": "Child agent's final assistant text (collected from the event stream).",
        },
        {
            "name": "child_session_id",
            "type": "string",
            "description": "Child turn's session id (TurnContext.session_id on the child ctx).",
        },
        {
            "name": "session_id",
            "type": "string",
            "description": "Stable session identifier for the governed turn.",
        },
        {
            "name": "turn_id",
            "type": "string",
            "description": "Stable turn identifier (ADK invocation_id at the boundary).",
        },
    ],
    "spawn": [
        {
            "name": "request.role",
            "type": "string",
            "description": "Subagent role tag carried in ChildTaskRequest.role (default 'general').",
        },
        {
            "name": "request.metadata",
            "type": "object",
            "description": "Opaque parent->child handoff dict (ChildTaskRequest.metadata); well-known keys include contextPlanDigest, parentToolNames, parentMemoryMode, spawnDepth, allowedTools, recipeRefs.",
        },
    ],
}


# Per the discovery's URL/path alias keys, used as honest hints when the
# wizard authors a domain / path matcher without a specific tool target.
_URL_ALIAS_HINTS: list[RuntimeField] = [
    {
        "name": f"tool_input.{key}",
        "type": "string",
        "description": (
            "URL-bearing argument key (matched via _URL_ARG_KEYS). The runtime "
            "extracts the host via urlparse(value).hostname.lower() for "
            "domain / domain_allowlist matchers."
        ),
    }
    for key in _URL_ARG_KEYS
]


_PATH_ALIAS_HINTS: list[RuntimeField] = [
    {
        "name": f"tool_input.{key}",
        "type": "string",
        "description": (
            "Path-bearing argument key (matched via _PATH_ARG_KEYS). "
            "URL-shaped values are rejected; normpath is applied before the "
            "path / path_allowlist matcher fires."
        ),
    }
    for key in _PATH_ARG_KEYS
]


def _tool_input_fields(
    tool_registry: Any | None,
    tool: str | None,
) -> list[RuntimeField]:
    """Return per-tool ``tool_input.<arg>`` fields for the picker.

    When ``tool`` is given and resolvable in the registry, expand the
    manifest's ``input_schema['properties']`` keys (one chip per key, with
    the JSON-Schema ``type`` and ``description`` if present).

    When ``tool`` is missing or unresolvable, fall back to the generic
    ``tool_input.*`` marker so the operator sees the variable exists, plus
    the URL/path alias hints which are the runtime's actual matcher inputs.
    Callers can layer the alias hints onto the result by passing
    ``include_url_aliases`` / ``include_path_aliases`` through
    :func:`fields_for_context`.
    """
    if not isinstance(tool, str) or not tool.strip():
        return [
            {
                "name": "tool_input.*",
                "type": "object",
                "description": (
                    "Per-tool argument dict; pick a specific tool above to see "
                    "the real argument keys from its manifest input_schema."
                ),
            }
        ]
    if tool_registry is None:
        return [
            {
                "name": "tool_input.*",
                "type": "object",
                "description": "Tool registry unavailable in this runtime context.",
            }
        ]
    try:
        manifest = tool_registry.resolve(tool)
    except Exception:  # noqa: BLE001 — fail-open per module contract
        return []
    if manifest is None:
        return []
    schema = getattr(manifest, "input_schema", None)
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return [
            {
                "name": "tool_input.*",
                "type": "object",
                "description": f"Tool {tool!r} declares no argument properties.",
            }
        ]
    fields: list[RuntimeField] = []
    for key in sorted(properties):
        prop = properties[key]
        if isinstance(prop, dict):
            type_str = str(prop.get("type") or "any")
            desc = str(prop.get("description") or "")
        else:
            type_str = "any"
            desc = ""
        fields.append(
            {
                "name": f"tool_input.{key}",
                "type": type_str,
                "description": desc,
            }
        )
    return fields


def _evidence_field_chips() -> list[RuntimeField]:
    """Surface ``evidence:<type>.fields.<key>`` chips from F2's hint table.

    Reads :func:`magi_agent.customize.shacl_compiler.available_fields` so the
    chip menu and the SHACL compiler stay in lockstep. Types with an honest
    empty hint list (producer unverified) are still surfaced with a single
    ``evidence:<type>.fields.*`` marker so the operator knows the type exists
    but no fields are confidently known.
    """
    try:
        from magi_agent.customize.shacl_compiler import available_fields
    except Exception:  # noqa: BLE001
        return []
    try:
        catalog = available_fields()
    except Exception:  # noqa: BLE001
        return []
    chips: list[RuntimeField] = []
    for entry in catalog:
        etype = entry.get("evidenceType")
        if not isinstance(etype, str) or not etype:
            continue
        fields = entry.get("fields")
        desc_base = entry.get("description") or ""
        if isinstance(fields, list) and fields:
            for key in fields:
                chips.append(
                    {
                        "name": f"evidence:{etype}.fields.{key}",
                        "type": "string",
                        "description": (
                            f"{etype}.{key} — SHACL predicate magi:field_{key}."
                            + (f" {desc_base}" if desc_base else "")
                        ).strip(),
                    }
                )
        else:
            chips.append(
                {
                    "name": f"evidence:{etype}.fields.*",
                    "type": "object",
                    "description": (
                        f"{etype} has no verified field hints in this runtime "
                        "(producer unverified). Authoring a field-shaped check "
                        "against this type may silently never fire."
                    ).strip(),
                }
            )
    return chips


def _pre_final_judgment_fields() -> list[RuntimeField]:
    """Pre-final llm_criterion variables (final_text + turn_summary)."""
    return [
        {
            "name": "final_text",
            "type": "string",
            "description": (
                "The candidate final assistant text; passed as draft_text to "
                "criterion_engine.evaluate_criterion by the pre_final judge."
            ),
        },
        {
            "name": "turn_summary",
            "type": "string",
            "description": (
                "Per-spec variable; no concrete runtime producer is wired yet. "
                "Reference at your own risk — the judge currently sees only "
                "final_text."
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Public: fields_for_context
# ---------------------------------------------------------------------------


def fields_for_context(
    lifecycle: str,
    condition: str,
    *,
    tool: str | None = None,
    tool_registry: Any | None = None,
) -> list[RuntimeField]:
    """Return the chip menu for a (lifecycle, condition, tool?) tuple.

    Parameters
    ----------
    lifecycle:
        One of ``before_tool_use`` / ``after_tool_use`` / ``pre_final`` /
        ``on_user_prompt_submit`` / ``on_subagent_stop`` / ``spawn``. An
        unknown lifecycle returns ``[]`` (fail-open).
    condition:
        The wizard ``conditionKind`` value (``regex``, ``contentMatch``,
        ``path``, ``pathAllowlist``, ``domain``, ``domainAllowlist``,
        ``llm_criterion``, ``shacl_constraint``, ``shacl``, ``field_constraint``,
        ``evidence_ref``, ``capability_scope``). Unknown values return ``[]``.
    tool:
        Optional tool name; when given, ``tool_input.*`` expansion uses the
        tool's manifest input_schema. When omitted, alias hints (URL/path)
        are surfaced instead for the relevant condition kinds.
    tool_registry:
        The runtime's ``ToolRegistry`` (optional). When ``None``, tool-input
        expansion degrades to the generic marker chip.

    Returns
    -------
    list[RuntimeField]
        Ordered list of {name, type, description} chips. Empty list on an
        unknown tuple — caller treats empty as "no chips, fall back to plain
        text input".
    """
    if not isinstance(lifecycle, str) or not isinstance(condition, str):
        return []

    base = list(_LIFECYCLE_BASE_FIELDS.get(lifecycle, []))
    if not base and lifecycle not in _LIFECYCLE_BASE_FIELDS:
        return []

    cond = condition.strip()

    # ---- before_tool_use -------------------------------------------------
    if lifecycle == "before_tool_use":
        if cond in ("regex", "contentMatch"):
            return base + _tool_input_fields(tool_registry, tool)
        if cond in ("path", "pathAllowlist", "path_allowlist"):
            chips = base + _tool_input_fields(tool_registry, tool)
            chips.extend(_PATH_ALIAS_HINTS)
            return chips
        if cond in ("domain", "domainAllowlist", "domain_allowlist"):
            chips = base + _tool_input_fields(tool_registry, tool)
            chips.extend(_URL_ALIAS_HINTS)
            return chips
        return []

    # ---- after_tool_use --------------------------------------------------
    if lifecycle == "after_tool_use":
        if cond in (
            "regex",
            "contentMatch",
            "llm_criterion",
        ):
            return base + _tool_input_fields(tool_registry, tool)
        return []

    # ---- pre_final -------------------------------------------------------
    if lifecycle == "pre_final":
        if cond in ("evidence_ref", "verifier_passed"):
            return base + _evidence_field_chips()
        if cond in ("shacl_constraint", "shacl", "field_constraint"):
            return base + _evidence_field_chips()
        if cond == "llm_criterion":
            return base + _pre_final_judgment_fields()
        return []

    # ---- on_user_prompt_submit (Tier 2, audit-only) ----------------------
    if lifecycle == "on_user_prompt_submit":
        if cond == "llm_criterion":
            return base
        return []

    # ---- on_subagent_stop (Tier 2, audit-only) ---------------------------
    if lifecycle == "on_subagent_stop":
        if cond == "llm_criterion":
            return base
        return []

    # ---- spawn (F4 capability_scope) ------------------------------------
    if lifecycle == "spawn":
        if cond == "capability_scope":
            return base
        return []

    return []


__all__ = ["RuntimeField", "fields_for_context"]
