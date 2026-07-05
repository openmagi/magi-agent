from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, MutableSequence
from datetime import UTC, datetime, tzinfo
import html
import logging
import math
import os
import posixpath
import re
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from magi_agent.harness.resolved import ResolvedHarnessPresetState
    from magi_agent.hooks.bus import HookBus

logger = logging.getLogger(__name__)


TOKEN_LIMIT_FOR_COMPACTION = 150_000
REPLY_PREVIEW_MAX_CHARS = 200
MAX_IMAGE_BLOCK_BYTES = 5_000_000
MAX_IMAGE_BLOCK_COUNT = 12
MAX_IMAGE_BLOCK_TOTAL_BYTES = 20_000_000
ATTACHMENT_DISPLAY_MAX_CHARS = 240
# Mirror: gate5b4c3_shadow_generation_contract.py defines an identical constant.
# Both definitions are intentional — importing across the runtime/shadow boundary
# would create a coupling that is deliberately avoided. Keep both in sync.
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset(
    ("image/jpeg", "image/png", "image/gif", "image/webp")
)

RUNTIME_MODEL_IDENTITY_OPEN = '<runtime_model_identity hidden="true">'
RUNTIME_MODEL_IDENTITY_CLOSE = "</runtime_model_identity>"
_RUNTIME_TEMPORAL_CONTEXT_PATTERN = re.compile(
    r'<runtime_temporal_context hidden="true">[\s\S]*?</runtime_temporal_context>'
)
# Render order + header for each self-identity slot. Slots are populated by
# ``magi_agent.cli.identity.load_identity`` from the ``.magi`` namespace.
# ``soul`` is LEGACY: SOUL.md is no longer read into the prompt (IDENTITY.md is
# the self-identity file), but the slot is retained last so hook/operator paths
# that still inject a ``soul`` section keep rendering. An unpopulated slot is
# skipped by ``_render_identity_system``.
_IDENTITY_SECTION_ORDER = (
    ("bootstrap", "BOOTSTRAP"),
    ("identity", "IDENTITY"),
    ("user", "USER"),
    ("learning", "LEARNING"),
    ("agents", "AGENTS"),
    ("soul", "SOUL"),
)
# E-4: ``_KNOWN_TOKEN_LIMITS`` lives in ``context/_token_window_table.py``, which
# now DERIVES the table from ``ModelCatalog.builtin()`` at import time (the leaf
# is no longer stdlib-only — the catalog pulls ``pydantic`` ->
# ``typing_extensions`` -> ``asyncio``/``socket``/``subprocess``, all forbidden by
# the message_builder import-purity contract). So the table is imported LAZILY at
# its single use site in ``_context_window_token_budget`` below, keeping the
# top-level ``message_builder`` import free of the catalog/pydantic edge while the
# structural follow-up routes everyone through ``ModelCatalog.context_window``
# (E-1/E-3). External importers of ``message_builder._KNOWN_TOKEN_LIMITS`` keep
# working via the lazy module ``__getattr__`` below, which resolves the SAME
# object from the leaf on first access without eagerly importing it.


def __getattr__(name: str) -> object:
    # Lazy re-export of ``_KNOWN_TOKEN_LIMITS``. Only fires on explicit attribute /
    # ``from ... import`` access, so the bare
    # ``import magi_agent.runtime.message_builder`` stays free of the catalog edge.
    if name == "_KNOWN_TOKEN_LIMITS":
        from magi_agent.context._token_window_table import _KNOWN_TOKEN_LIMITS

        return _KNOWN_TOKEN_LIMITS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_OPENAI_COMPAT_CONTEXT_WINDOW = 131_072
_OPENAI_COMPAT_MODEL_PREFIXES = (
    "ollama/",
    "vllm/",
    "tgi/",
    "custom/",
    "localai/",
    "openrouter/",
)
_CREDENTIAL_FIELD_PATTERN = re.compile(
    r"(?i)\b(?:x-)?(?:auth(?:entication)?|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|bot[_-]?token|token|cookie|session|password|passwd|"
    r"secret|signature|sig|credential)[\w-]*\s*(?:=|:)\s*[^&\s,;)]*"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_AUTH_HEADER_PATTERN = re.compile(
    r"(?i)\bauthorization\s*:\s*(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]+"
)
_BASIC_AUTH_PATTERN = re.compile(r"(?i)\bbasic\s+[A-Za-z0-9._~+/=-]+")
_COOKIE_HEADER_PATTERN = re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*[^,)]*")
_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s<>'\")]+")
_SIGNED_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)[?&][A-Za-z0-9_.~-]*(?:auth|api[_-]?key|cookie|credential|expires|"
    r"secret|security-token|session|signature|sig|signedheaders|token)"
    r"[A-Za-z0-9_.~-]*=[^&\s,;)]*"
)
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"\b(?:bot)?\d{6,}:[A-Za-z0-9_-]{8,}\b")
_COMMON_SECRET_TOKEN_PATTERN = re.compile(
    r"\b(?:sk-proj-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]+|AIza[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]+)\b"
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:/(?:Users|home|workspace|private|var|etc|root|tmp|mnt|secrets)"
    r"(?:/[^\s,;:)]+)+|[A-Za-z]:\\(?:Users|home|workspace|private|var|tmp)"
    r"(?:\\[^\s,;:)]+)+)"
)

INCOGNITO_MEMORY_MODE_BLOCK = "\n".join(
    [
        '<memory_mode hidden="true">',
        "memory_mode: incognito",
        "This channel keeps chat history, but long-term memory is disabled.",
        "Do not read, search, summarize, or write long-term memory files such as memory/*, MEMORY.md, SCRATCHPAD.md, WORKING.md, or TASK-QUEUE.md.",
        "Do not ask another agent or tool to persist this channel's conversation into long-term memory.",
        "</memory_mode>",
    ]
)

READ_ONLY_MEMORY_MODE_BLOCK = "\n".join(
    [
        '<memory_mode hidden="true">',
        "memory_mode: read_only",
        "Existing long-term memory may be read for context.",
        "Do not write, summarize, checkpoint, or persist this channel's conversation into long-term memory files such as memory/*, MEMORY.md, SCRATCHPAD.md, WORKING.md, or TASK-QUEUE.md.",
        "Do not ask another agent or tool to persist this channel's conversation into long-term memory.",
        "</memory_mode>",
    ]
)

OUTPUT_RULES_BLOCK = "\n".join(
    [
        "<output-rules>",
        "CRITICAL: The user can only see your TEXT output, not your thinking.",
        "",
        "1. Your thinking block is for internal reasoning ONLY — planning, analysis, deciding what to do.",
        "2. Everything you want the user to read MUST appear in your text response.",
        "3. NEVER put user-facing content (answers, analysis, questions, summaries) only in thinking.",
        "4. If your thinking contains a detailed response, you MUST reproduce the key content in your text output.",
        "5. A text response that is just a brief closing while thinking had the full analysis is a FAILURE.",
        "6. NEVER include raw tool output or JSON in your text response.",
        "</output-rules>",
    ]
)

DEFERRAL_PREVENTION_BLOCK = "\n".join(
    [
        "<deferral-prevention>",
        "Default to ACTION via tools, not narration. The fastest path from user",
        "intent to a delivered result is a tool call, not a plan written in text.",
        "",
        "Forbidden patterns — these end the turn with nothing produced:",
        "- \"Refreshed Plan\" / \"Remaining Work\" / \"Next Action: I will …\"",
        "- \"I'll now fetch / search / read / spawn / write / run …\"",
        "- \"Let me check / let me look / let me see …\" followed by no tool call",
        "- \"다음 단계: …\" / \"다음으로는 …\" / \"~~할 것입니다\" / \"~~하겠습니다\"",
        "- Any sentence that describes the next step instead of executing it.",
        "",
        "When the next step is a tool call, call the tool in the SAME response.",
        "Do not restate the plan; the user will see the tool's effect.",
        "",
        "Multi-step requests:",
        "- Execute step 1 NOW with a tool call. Do not list every step as text first.",
        "- After each tool call, decide the next step from the result and execute it.",
        "- Keep executing step by step until the entire user task is complete,",
        "  then give the final answer. Only stop early on a concrete blocker.",
        "",
        "What counts as done:",
        "- Every concrete deliverable the user asked for has been produced via",
        "  tool calls AND verified (read it back, run the test, check the output).",
        "- A text response that DESCRIBES what would be done is NOT done.",
        "",
        "Code or file content rule (decisive for tool-shy models): code or file",
        "content that only appears in your text response is NOT saved to the",
        "filesystem and will NOT take effect. Use FileWrite / FileEdit /",
        "PatchApply / the appropriate tool to actually create or modify it.",
        "",
        "If you genuinely cannot finish:",
        "- State the concrete blocker in one sentence (what failed, what is missing).",
        "- Do not promise to \"do it next turn\". Never wrap a failure in a fake plan.",
        "- Do not retry failing commands in a sleep loop — diagnose the root cause.",
        "</deferral-prevention>",
    ]
)

CODING_DISCIPLINE_BLOCK = "\n".join(
    [
        "<coding-discipline>",
        "When writing or modifying code:",
        "",
        "Architecture:",
        "- Don't add features, refactor, or introduce abstractions beyond what the task requires.",
        "- Three similar lines is better than a premature abstraction. No half-finished implementations.",
        "- A bug fix doesn't need surrounding cleanup. A one-shot operation doesn't need a helper.",
        "",
        "Error handling:",
        "- Don't add error handling, fallbacks, or validation for scenarios that can't happen.",
        "- Trust internal code and framework guarantees. Only validate at system boundaries.",
        "",
        "Comments:",
        "- Default to no comments. Only add one when the WHY is non-obvious.",
        "- Don't explain WHAT the code does (well-named identifiers do that).",
        "- Don't reference the current task, fix, or callers in comments.",
        "",
        "Scope:",
        "- Don't design for hypothetical future requirements.",
        "- Don't add backwards-compatibility shims when you can just change the code.",
        "- If something is unused, delete it completely. No renaming to _unused.",
        "</coding-discipline>",
    ]
)

CODING_WORKFLOW_BLOCK = "\n".join(
    [
        "<coding-workflow>",
        "When fixing a bug or changing existing behavior, work reproduce-first:",
        "",
        "1. Analyze the relevant files before editing anything.",
        "2. Write a small script or test that reproduces the issue; run it and confirm it fails.",
        "3. Edit the source code to fix the issue.",
        "4. Re-run the reproduction script/test and confirm the fix.",
        "5. Check edge cases the fix could affect.",
        "6. Run the repo's focused tests for the files you touched.",
        "</coding-workflow>",
    ]
)

# Per-family semantic coding hints (PR10). Small, distilled blocks — one per
# provider family magi routes to — encoding that family's known coding failure
# mode (the gist of OpenCode's per-model prompt swaps, NOT a full prompt copy).
# Injected only on the coding-agent path AND only when the model-aware flag is
# on; live in the STATIC (cacheable) region so prompt caching is preserved.
def _coding_model_hint_block(family: str, body: str) -> str:
    return "\n".join(
        [
            f'<coding-model-hint family="{family}">',
            body,
            "</coding-model-hint>",
        ]
    )


CODING_MODEL_HINT_BLOCK: dict[str, str] = {
    "openai": _coding_model_hint_block(
        "openai",
        "- Before relying on an API/library existence or its current signature, "
        "verify by reading the actual code or docs; your training may be stale.",
    ),
    "google": _coding_model_hint_block(
        "google",
        "- Always use absolute file paths in tool calls; relative paths are "
        "unreliable here.",
    ),
    "fireworks": _coding_model_hint_block(
        "fireworks",
        "- Code only takes effect when written to disk via tools. Text in your "
        "reply is not saved to disk; you must call the write/edit tools.",
    ),
    # No "anthropic" entry: claude already follows the structured blocks above,
    # so .get("anthropic", "") returns "" — a no-op hint would only waste tokens.
}


def _coding_model_hint_for(model: str) -> str:
    """Return the family-keyed coding hint for *model*, or ``""`` for default.

    Detection reuses :func:`provider_adapter.detect_provider_family` so the
    family mapping stays single-sourced. The ``default`` family gets no hint.
    """
    from magi_agent.prompt.provider_adapter import detect_provider_family

    family = detect_provider_family(model).value
    return CODING_MODEL_HINT_BLOCK.get(family, "")


OUTPUT_EFFICIENCY_BLOCK = "\n".join(
    [
        "<output-efficiency>",
        "Length targets (not hard limits — use judgment):",
        "- Between tool calls: ≤30 words. State what you found or what you're doing next.",
        "- Final response: ≤150 words unless the task requires detailed explanation.",
        "- Status updates: one sentence per update.",
        "- Don't narrate your reasoning process. State results and decisions directly.",
        "- Don't summarize what you just did at the end — the user can see the tool calls.",
        "- Match response length to task: a simple question gets a direct answer, not headers and sections.",
        "</output-efficiency>",
    ]
)

CITATION_CONVENTION_BLOCK = "\n".join(
    [
        "<citation-convention>",
        "When you state a fact that comes from a web / KB / file tool result,",
        "cite it INLINE using standard markdown link syntax right after the fact:",
        "",
        "    Tesla revenue grew 12% YoY in Q3 ([10-K filing](https://www.sec.gov/...)).",
        "",
        "Rules:",
        "- Use the EXACT URL the tool result returned. Never invent or fabricate a URL.",
        "- One citation per fact is enough — don't repeat the same link multiple times for restated material.",
        "- Cite the most specific page you actually inspected, not a generic homepage.",
        "- The link label is a brief human-readable hint (e.g. publisher, doc type), not a full sentence.",
        "- Plain interpretation, reasoning, or your own opinion needs no citation.",
        "- If you're unsure whether a fact came from a tool result vs. prior knowledge, do NOT add a citation.",
        "",
        "The dashboard turns matching links into clickable source chips with hover previews,",
        "so accurate citations directly improve the user's ability to audit your answer.",
        "</citation-convention>",
    ]
)

# Static ``<source_citation>`` system-prompt block (source-citation feature).
# FIXED BYTES: no session ids, no source lists, no counts, so the cached prompt
# prefix stays hit-stable across turns. This is the registry-backed ``[src_N]``
# convention that REPLACES ``CITATION_CONVENTION_BLOCK`` when
# MAGI_SOURCE_CITATION_ENABLED is on (the two are mutually exclusive; see the swap
# in ``_assemble_prompt_sections``). The canonical bytes live here (the layer that
# owns system-prompt blocks) because message_builder may not import
# ``magi_agent.tools``; ``tools.web_search_tools._SOURCE_CITATION_GUIDANCE`` is
# kept byte-identical by a drift-guard test.
SOURCE_CITATION_GUIDANCE_BLOCK = (
    "<source_citation>\n"
    "External reads (web search, web fetch, knowledge base, browser, document\n"
    "reads) are tagged with a stable citation id shown in the tool result, of\n"
    "the form [src_N] (for example [src_3]). When you state a fact you got from\n"
    "one of those sources, cite it inline as [src_N] immediately after the\n"
    "claim.\n"
    "Rules:\n"
    "1. Only cite an id that actually appeared in a tool result this session.\n"
    "   Never invent an id.\n"
    "2. Prefer searching before asserting specific figures, dates, names, or\n"
    "   quoted statistics. If you do not have a source for a specific figure,\n"
    "   look it up first, then cite it.\n"
    "3. Attach the id to the sentence that uses the figure, not to a distant\n"
    "   sentence.\n"
    "</source_citation>"
)

ACTION_SAFETY_BLOCK = "\n".join(
    [
        "<action-safety>",
        "Before taking an action, consider its reversibility and blast radius:",
        "",
        "Freely take (no confirmation needed):",
        "- Reading files, searching, running tests, linting",
        "- Editing files (reversible via git)",
        "- Creating new files",
        "",
        "Confirm with user first:",
        "- Destructive operations: deleting files/branches, dropping tables, rm -rf",
        "- Hard-to-reverse: force-push, git reset --hard, amending published commits",
        "- Visible to others: pushing code, creating/closing PRs, sending messages",
        "- Deploying to production or staging environments",
        "",
        "If uncertain about reversibility, ask. The cost of pausing is low; the cost of an unwanted action is high.",
        "</action-safety>",
    ]
)


def format_reply_preamble(reply_to: Mapping[str, object] | object) -> str:
    raw_role = _field(reply_to, "role", default="user")
    role = raw_role if raw_role in ("user", "assistant") else "user"
    preview = str(_field(reply_to, "preview", default=""))
    collapsed = re.sub(r"\s+", " ", preview).strip()
    if len(collapsed) > REPLY_PREVIEW_MAX_CHARS:
        collapsed = f"{collapsed[:REPLY_PREVIEW_MAX_CHARS]}…"
    return f'[Reply to {role}: "{collapsed}"]'


def build_runtime_temporal_context(
    now: datetime | None = None,
    timezone: str | tzinfo | None = None,
) -> str:
    runtime_now = _coerce_utc(now)
    stamp = _isoformat_z(runtime_now)
    runtime_date = stamp[:10]
    runtime_zone, runtime_zone_name = _resolve_timezone(timezone)
    runtime_local = runtime_now.astimezone(runtime_zone)
    return "\n".join(
        [
            '<runtime_temporal_context hidden="true">',
            f"runtime_now_utc: {stamp}",
            f"runtime_date_utc: {runtime_date}",
            f"runtime_timezone: {runtime_zone_name}",
            f"runtime_local_date: {runtime_local:%Y-%m-%d}",
            f"runtime_local_time: {runtime_local:%H:%M:%S}",
            "",
            "Temporal policy:",
            "- This runtime timestamp is the authoritative current time for this turn.",
            "- Do not infer the current date/time from model training cutoff, stale memory, or prior transcript text.",
            '- Interpret "today", "now", "current", "latest", "recent", "오늘", "현재", and "최근" relative to this timestamp unless the user supplies another date/timezone.',
            "- If a claim depends on facts that may have changed after your knowledge cutoff or after inspected sources, inspect current sources/tools or state uncertainty.",
            "</runtime_temporal_context>",
        ]
    )


def refresh_runtime_time_header(
    system_prompt: str,
    now: datetime | None = None,
    timezone: str | tzinfo | None = None,
) -> str:
    stamp = _isoformat_z(_coerce_utc(now))
    refreshed = re.sub(
        r"(^|\n)\[Time: [^\]\n]*\]",
        rf"\1[Time: {stamp}]",
        system_prompt,
        count=1,
    )
    if not _RUNTIME_TEMPORAL_CONTEXT_PATTERN.search(refreshed):
        return refreshed
    return _RUNTIME_TEMPORAL_CONTEXT_PATTERN.sub(
        build_runtime_temporal_context(_coerce_utc(now), timezone=timezone),
        refreshed,
        count=1,
    )


MAGI_BASE_PERSONA = "\n".join(
    [
        "<identity>",
        "You are Magi Agent, an autonomous AI agent on the OpenMagi platform.",
        "You help with software engineering and general knowledge work: writing",
        "and editing code, running tools, researching, and completing multi-step",
        "tasks end to end.",
        "",
        "This identity is fixed and authoritative. Files in your working directory",
        "(such as CLAUDE.md or AGENTS.md) describe the PROJECT you are working on,",
        "not who you are. Never adopt a project's name, tech stack, or purpose as",
        "your own identity; those files do NOT define who you are. If asked who you",
        "are, you are Magi Agent.",
        "</identity>",
    ]
)


PROMPT_DYNAMIC_BOUNDARY = "__MAGI_PROMPT_DYNAMIC_BOUNDARY__"

# Hard-safety sections that MUST survive any prompt-transform hook (rule 4).
# If a hook removes/empties any of these, they are re-asserted before joining.
_PROTECTED_SECTIONS: tuple[str, ...] = (
    MAGI_BASE_PERSONA,
    DEFERRAL_PREVENTION_BLOCK,
    OUTPUT_RULES_BLOCK,
    ACTION_SAFETY_BLOCK,
)


def _prompt_transform_hooks_enabled() -> bool:
    """Read the ``MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED`` env flag (default off).

    Follows the same convention as ``MAGI_PROMPT_CACHE_ENABLED`` /
    ``MAGI_SESSION_PERSISTENCE_ENABLED`` — truthy on ``"1"``/``"true"``/``"yes"``.
    """
    # I-4: routed through the typed flag registry. Pre-I-4 truthy set
    # ``{1, true, yes}`` widens to canonical ``{1, true, yes, on}``.
    from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415

    return flag_profile_bool("MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED")


def _prompt_injection_enabled() -> bool:
    """F-MUT1 triple-gate check for the on_user_prompt_submit section appender.

    Returns ``True`` only when ``MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`` is
    strict-truthy ON AND the two customize prerequisites
    (``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``)
    resolve ON via the profile-aware reader. Fail-open: any import / read
    error returns ``False`` so the OFF path stays byte-identical.
    """
    try:
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_bool,
            flag_profile_bool,
        )
    except Exception:
        return False
    try:
        return (
            flag_bool("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
            and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
        )
    except Exception:
        return False


def _maybe_apply_prompt_injection_sections(sections: list[str]) -> list[str]:
    """F-MUT1: append ``prompt_injection`` rule sections to *sections*.

    Loads the persisted Customize overrides, builds a resolved
    :class:`CustomizeVerificationPolicy`, and appends one section per enabled
    ``prompt_injection`` rule with ``firesAt == "on_user_prompt_submit"`` and
    ``target == "system_prompt"`` (mode=append, v1). Returns the input
    unchanged when:

    * the F-MUT1 master flag resolves OFF,
    * no matching rules are authored,
    * any error walks the customize-store / import chain (fail-open).

    Pure / read-only / fail-open — a malformed rule never breaks prompt
    assembly. The append happens AFTER the BEFORE_SYSTEM_PROMPT hook replace
    branch so a hook-authored full replacement composes deterministically
    with the rule-driven appends.
    """
    try:
        if not _prompt_injection_enabled():
            return sections
        from magi_agent.customize.prompt_injection import (  # noqa: PLC0415
            apply_prompt_injection_to_prompt_sections,
        )
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        rules = policy.enabled_prompt_injection_rules(
            fires_at="on_user_prompt_submit"
        )
        if not rules:
            return sections
        return apply_prompt_injection_to_prompt_sections(sections, rules)
    except Exception:  # noqa: BLE001 — fail-open
        return sections


def _available_subagent_models_block() -> str:
    """System-prompt block listing the routes a child spawn may target.

    Empty unless live serve sub-agents are enabled (``live_subagents_serve_enabled``)
    AND at least one route resolves — so a deployment without sub-agents is
    byte-identical. Tells the model its real, routable models so it stops
    inventing names or claiming it cannot pick a sub-agent's model. Fail-soft.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            gate5b_live_subagents_flag_on,
        )
        from magi_agent.runtime.child_runner_live import (  # noqa: PLC0415
            is_live_child_runner_enabled,
        )
        from magi_agent.runtime.model_tiers import (  # noqa: PLC0415
            available_child_model_routes,
        )

        # Mirror transport.live_subagents_serve_enabled WITHOUT importing transport
        # (message_builder must stay above the transport layer): the serve flag
        # AND the kill-switch-aware live child-runner master gate. The flag read
        # lives in the config allowlist (no inline env read here).
        if not (
            gate5b_live_subagents_flag_on(os.environ)
            and is_live_child_runner_enabled(os.environ)
        ):
            return ""
        routes = available_child_model_routes(os.environ)
        if not routes:
            return ""
        listed = "\n".join(f"- {route}" for route in routes)
        return (
            "<available_subagent_models>\n"
            "You CAN delegate to sub-agents on specific models via SpawnAgent. "
            "Pass BOTH `provider` and `model` exactly as one of these routes "
            "(other names are rejected as child_model_route_unknown; omitting "
            "`provider` defaults to anthropic):\n"
            f"{listed}\n"
            "</available_subagent_models>"
        )
    except Exception:  # noqa: BLE001 — prompt assembly must never crash.
        return ""


def _okf_guidance_block(env: Mapping[str, str] | None = None) -> str:
    """System-prompt block inducing the agent to discover the OKF knowledge store.

    Empty unless the OKF master + lookup flags are BOTH enabled (default-OFF), so
    a deployment without a curated ``knowledge/okf`` store is byte-identical.
    Resolution is delegated to the single source of truth
    (:func:`~magi_agent.knowledge.okf.config.resolve_okf_config`); ``env`` is
    injectable so callers/tests stay hermetic. Fail-soft: any resolver error
    yields ``""`` (prompt assembly must never crash).
    """
    try:
        from magi_agent.knowledge.okf.config import (  # noqa: PLC0415
            resolve_okf_config,
        )

        config = resolve_okf_config(env=env)
        if not (config.master_enabled and config.lookup_enabled):
            return ""
    except Exception:  # noqa: BLE001 — fail-soft; no block on resolver error.
        return ""
    return (
        "<knowledge_okf>\n"
        "This workspace may include a curated knowledge store under "
        "`knowledge/okf/` (Open Knowledge Format). For domain or factual "
        "questions, consult it FIRST with the OkfLookup tool (pass a `query`) "
        "before relying on general knowledge; cite the returned document "
        "path/source.\n"
        "</knowledge_okf>"
    )


def _user_rules_block() -> str:
    """Operator-supplied advisory text configured in the Customize Guidance tab.

    Reads the trimmed text via
    :meth:`CustomizeVerificationPolicy.user_rules_advisory_text` (the canonical
    accessor over the ``user_rules`` overrides field). Wraps it in a
    ``<user_advisory_rules>`` fence with an honest header that tells the model
    these rules are NOT enforced by a hard gate — they are operator advisory
    only. The honest framing is deliberate: the model should know the
    difference between advisory text and the deterministic ``custom_rules``
    gates (gap 5 of the Customize Depth Enrichment design).

    Replaces the prior ``## User Rules ... Follow them`` markdown framing
    introduced in PR #603, which overstated enforcement strength and shared the
    same body with the new envelope, double-injecting the same text. Single
    source of truth for ``user_rules`` injection; ``guidance-panel.tsx``'s
    Advisory badge mirrors this framing for operators.

    Flag-gated by ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` (profile-aware
    default-ON in the full runtime profile, OFF under safe/eval). Empty (no
    section) when the flag is off, when the field is unset, on any error, or
    when the trimmed text is empty, so the prompt stays byte-identical under
    safe profiles. Fail-soft to ``""``.
    """
    from magi_agent.config.flags import flag_profile_bool


    if not flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
        return ""
    try:
        from magi_agent.customize.store import load_overrides
        from magi_agent.customize.verification_policy import (
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        text = policy.user_rules_advisory_text()
    except Exception:
        return ""
    if not text:
        return ""
    # Sanitize a literal closing fence in the body so an operator can't
    # prematurely close the envelope and confuse the model.
    safe_text = text.replace("</user_advisory_rules>", "</user_advisory_rules_>")
    return (
        "<user_advisory_rules>\n"
        "Operator advisory rules (advisory: not enforced by a hard gate; "
        "the model is asked to honor these where applicable).\n"
        "\n"
        f"{safe_text}\n"
        "</user_advisory_rules>"
    )


def _agent_mode_block() -> str:
    """Active agent MODE (posture) system-prompt injection.

    Reads the session-sticky active mode (customize ``active_agent_mode`` +
    ``agent_modes``) and injects its ``system_prompt`` as a dynamic prompt part,
    wrapped in an ``<agent_mode>`` fence with an honest header. A mode is an
    EXPLICIT operator selection, so this is opt-in by construction: no active
    mode ⇒ ``""`` ⇒ byte-identical. Uses the SAME dynamic-parts seam as
    ``_user_rules_block`` (operator-authored prompt text), so mode text inherits
    the same run-share / observability treatment as user rules (no new leak
    surface). Fail-soft to ``""``.

    No default-OFF env flag (per the abolish-default-OFF-inert policy): the gate
    is active-mode presence. A fleet-wide kill-switch can be layered later if
    hosted needs one.
    """
    try:
        from magi_agent.customize.modes import active_mode_id, get_mode
        from magi_agent.runtime.per_turn_agent_mode_context import (
            current_per_turn_agent_mode,
        )

        # An explicit per-send selection (chat request ``agentMode``) WINS over
        # the operator's stored sticky default; fall back to the stored active
        # mode when the request did not specify one.
        mode_id = current_per_turn_agent_mode() or active_mode_id()
        if not mode_id:
            return ""
        mode = get_mode(mode_id)
        if mode is None:
            return ""
        prompt = mode.system_prompt.strip()
    except Exception:
        return ""
    if not prompt:
        return ""
    # Sanitize a literal closing fence in BOTH the body and the display name
    # (the name is interpolated into the header) so neither can prematurely close
    # the envelope (mirrors _user_rules_block for the body).
    safe_text = prompt.replace("</agent_mode>", "</agent_mode_>")
    safe_name = mode.display_name.replace("</agent_mode>", "</agent_mode_>")
    return (
        "<agent_mode>\n"
        f"Active mode: {safe_name} (operator-selected posture; guidance "
        "for how to approach this session).\n"
        "\n"
        f"{safe_text}\n"
        "</agent_mode>"
    )


def _credentials_awareness_block() -> str:
    """Redacted summary of registered Agent Vault credentials, injected each turn.

    Flag-gated by ``MAGI_CREDENTIAL_AWARENESS_ENABLED`` (profile-aware default-ON
    in the full runtime profile, OFF under safe/eval). Lets the agent acknowledge
    that a credential EXISTS (service, auth scheme, approval requirement) while
    NEVER exposing the secret value: the vault injects it on egress to the
    matching service and the agent never sees it. Empty (no section) when the
    flag is off, on any error, or with no usable credentials, so the prompt stays
    byte-identical to before. Fail-soft to "".
    """
    from magi_agent.config.flags import flag_profile_bool


    if not flag_profile_bool("MAGI_CREDENTIAL_AWARENESS_ENABLED"):
        return ""
    try:
        from magi_agent.credentials_admin.store import load_credentials

        credentials = load_credentials().get("credentials") or []
    except Exception:
        return ""
    rows = [
        cred
        for cred in credentials
        if isinstance(cred, Mapping) and str(cred.get("status")) != "revoked"
    ]
    if not rows:
        return ""
    lines: list[str] = []
    for cred in rows:
        service = str(cred.get("service") or "unknown")
        label = str(cred.get("label") or "").strip()
        scheme = str(cred.get("auth_scheme") or "").strip()
        host = str(cred.get("host") or "").strip()
        identifier = f"{service}/{label}" if label else service
        detail_parts = [part for part in (scheme, f"via {host}" if host else "") if part]
        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        approval = (
            " (approval required before each use)" if cred.get("requires_approval") else ""
        )
        lines.append(f"- {identifier}{detail}{approval}")
    body = "\n".join(lines)
    return (
        "## Registered Credentials (Agent Vault)\n\n"
        "These third-party credentials are registered for this agent. You CANNOT "
        "see their secret values. The vault injects each secret automatically "
        "when you make an outbound request to the matching service, so you never "
        "handle the raw key. If a credential is marked \"approval required\", your "
        "request is held until the user approves it in the dashboard Credentials "
        "tab. Never tell the user a listed credential is missing or that you have "
        "no record of it; instead explain that it exists and is used on egress.\n\n"
        f"{body}"
    )


def _assemble_prompt_sections(
    *,
    session_key: str,
    turn_id: str,
    identity: Mapping[str, object] | None,
    channel: Mapping[str, object] | object | None,
    user_message: Mapping[str, object] | object | None,
    runtime_now: datetime,
    timezone: str | tzinfo | None,
    coding_agent: bool,
    model: str,
    model_aware_prompts_enabled: bool,
    memory_snapshot_block: str = "",
    okf_guidance_block: str = "",
    env: Mapping[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Single source of truth for system-prompt section assembly.

    Returns ``(static_parts, dynamic_parts)``.  Both
    :func:`build_system_prompt` and :func:`build_system_prompt_blocks` route
    through this helper so the section content and ordering stay identical
    across cache modes (rule 6).  The caller is responsible for inserting the
    :data:`PROMPT_DYNAMIC_BOUNDARY` between the two lists.
    """
    channel_type = _channel_type(channel)
    session_header = "\n".join(
        [
            f"[Session: {session_key}]",
            f"[Turn: {turn_id}]",
            f"[Time: {_isoformat_z(runtime_now)}]",
            f"[Channel: {channel_type}]",
        ]
    )

    static_parts: list[str] = [MAGI_BASE_PERSONA]
    rendered_identity = _render_identity_system(
        identity or {},
        model=model,
        model_aware_prompts_enabled=model_aware_prompts_enabled,
    )
    if rendered_identity:
        static_parts.append(rendered_identity)
    rendered_project_context = _render_project_context(identity or {})
    if rendered_project_context:
        static_parts.append(rendered_project_context)
    # Single citation convention (source-citation reconciliation). When
    # MAGI_SOURCE_CITATION_ENABLED is on the runtime instructs the registry-backed
    # ``[src_N]`` convention (stable ids for dedup + verification), so the older
    # markdown-link ``CITATION_CONVENTION_BLOCK`` must NOT also ship: two mutually
    # exclusive conventions confuse the model and lower ``[src_N]`` compliance
    # (the Wave 3/4 render + gate only scan for canonical ``[src_N]``). The static
    # ``<source_citation>`` block takes the EXACT slot the markdown block held, so
    # the section count and ordering are unchanged (cache-prefix stable). When the
    # flag is off the markdown block is kept, byte-identical to before. This makes
    # EVERY assembler that routes through this helper (``build_system_prompt`` and
    # ``build_system_prompt_blocks``) carry exactly one convention.
    from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

    try:
        _source_citation_on = flag_profile_bool(
            "MAGI_SOURCE_CITATION_ENABLED", env=env
        )
    except Exception:  # noqa: BLE001 -- prompt assembly must never break
        _source_citation_on = False
    citation_convention_block = (
        SOURCE_CITATION_GUIDANCE_BLOCK
        if _source_citation_on
        else CITATION_CONVENTION_BLOCK
    )
    static_parts.extend([
        DEFERRAL_PREVENTION_BLOCK,
        OUTPUT_RULES_BLOCK,
        citation_convention_block,
        OUTPUT_EFFICIENCY_BLOCK,
        ACTION_SAFETY_BLOCK,
    ])
    subagent_models_block = _available_subagent_models_block()
    if subagent_models_block:
        static_parts.append(subagent_models_block)
    if coding_agent:
        static_parts.extend([CODING_DISCIPLINE_BLOCK, CODING_WORKFLOW_BLOCK])
        # PR10: semantic per-model coding hint, only when the model-aware flag
        # is on. Lives in the STATIC region (cacheable) alongside the other
        # coding blocks; default family contributes nothing (single body).
        if model_aware_prompts_enabled and model:
            hint = _coding_model_hint_for(model)
            if hint:
                static_parts.append(hint)
    # OKF knowledge-store guidance: caller-built, default-OFF capability block.
    # Appended after the coding blocks with the other capability hints; only
    # present when non-empty so the OFF path stays byte-identical.
    if okf_guidance_block:
        static_parts.append(okf_guidance_block)

    dynamic_parts: list[str] = [
        session_header,
        build_runtime_temporal_context(runtime_now, timezone=timezone),
    ]
    memory_mode_block = _memory_mode_block(channel)
    if memory_mode_block:
        dynamic_parts.append(memory_mode_block)
    if memory_snapshot_block:
        dynamic_parts.append(memory_snapshot_block)
    addendum = _system_prompt_addendum(user_message)
    if addendum:
        dynamic_parts.append(addendum)
    user_rules_block = _user_rules_block()
    if user_rules_block:
        dynamic_parts.append(user_rules_block)
    agent_mode_block = _agent_mode_block()
    if agent_mode_block:
        dynamic_parts.append(agent_mode_block)
    credentials_block = _credentials_awareness_block()
    if credentials_block:
        dynamic_parts.append(credentials_block)

    return static_parts, dynamic_parts


def _reassert_protected_sections(sections: list[str]) -> list[str]:
    """Canonicalise the hard-safety blocks at the FRONT of *sections* (rule 4).

    Presence alone is not enough: a hook can keep the exact protected-block
    strings while REORDERING them (e.g. moving ``OUTPUT_RULES_BLOCK`` to the
    bottom) or inserting adversarial text before them.  Track 16 rule 4 forbids
    hooks from being able to "remove, empty, OR REORDER away" the protected
    sections.  This function therefore:

      * strips EVERY occurrence of any protected block from the hook output
        (a hook may have duplicated or relocated them), then
      * prepends the protected blocks in canonical :data:`_PROTECTED_SECTIONS`
        order, so they appear exactly once each at the very front, and
      * keeps all non-protected sections in their original relative order
        after the protected prefix.

    Idempotent: an already-canonical list is returned unchanged.  Always
    returns a NEW list and never mutates the input.
    """
    protected = set(_PROTECTED_SECTIONS)
    remainder = [section for section in sections if section not in protected]
    canonical = [*_PROTECTED_SECTIONS, *remainder]
    if canonical != sections:
        # Either a protected block was dropped/emptied, or the hook reordered/
        # duplicated them (or inserted text ahead of them). Re-assertion fires.
        logger.warning(
            "promptTransform hook output was not canonical for protected "
            "sections; re-asserting %d hard-safety block(s) at the front",
            len(_PROTECTED_SECTIONS),
        )
    return canonical


def _estimate_prompt_tokens(sections: list[str]) -> int:
    """Deterministic char/4 token estimate over joined sections.

    Reuses the runtime's prevailing ``len(text) // 4`` heuristic (see
    ``context.token_tracker`` / ``context.auto_compact``) so the
    ``PromptTransform`` evidence's ``tokens_before``/``tokens_after`` are
    deterministic and dependency-free.
    """
    return sum(len(section) for section in sections) // 4


def _apply_prompt_transform(
    sections: list[str],
    *,
    hook_bus: "HookBus | None",
    harness_state: "ResolvedHarnessPresetState | None",
    hook_context: "object | None",
    model: str = "",
    provider: str | None = None,
    coding_agent: bool = False,
    evidence_sink: "Callable[[Mapping[str, object]], None] | None" = None,
) -> list[str]:
    """Fire the ``beforeSystemPrompt`` hook over *sections* exactly once.

    Behaviour:
      * flag off / no bus / no hooks  -> *sections* returned unchanged.
      * a ``replace`` result with a list[str] value -> that NEW list is used.
      * any non-list / malformed value -> fail-safe to original *sections*.
    After transform, hard-safety blocks are re-asserted (rule 4).

    The hook is given the current sections as an IMMUTABLE tuple via
    :attr:`HookContext.prompt_sections` (Track 16 §4) plus ``model``/
    ``provider`` so additive transforms can read existing sections and return a
    NEW list (e.g. ``[*context.prompt_sections, "Respond in Korean"]``).  The
    ``coding_agent`` flag is surfaced as ``policy_scope`` ("coding"/"general")
    since there is no dedicated agent-role field on ``HookContext``.

    *sections* is treated as immutable input (rule 3): hooks receive a tuple
    copy and must return a new list; this function never mutates the caller's
    list.

    When the flag is off (or no bus), this short-circuits BEFORE constructing
    any projected context, so disabled output stays byte-identical and free of
    per-call object allocation.

    Track-12 cache interaction: this transform runs over the STATIC sections,
    which form Track-12's cacheable byte-identical prefix. When the flag is ON
    and a hook actually replaces sections, that prefix is mutated, which LOWERS
    the prompt-cache hit rate. When the flag is OFF this short-circuits and the
    prefix is byte-identical, so there is no cache regression.

    F-MUT1: the ``prompt_injection`` mutator kind runs INDEPENDENTLY of the
    BEFORE_SYSTEM_PROMPT HookBus, so even the OFF-hook / OFF-bus short-circuit
    must consult F-MUT1 appended sections. See
    :func:`_maybe_apply_prompt_injection_sections` — when the F-MUT1 flag is
    OFF the helper returns its input unchanged (byte-identical) so the legacy
    short-circuit path stays zero-cost. When F-MUT1 is ON the helper composes
    appended sections regardless of the BEFORE_SYSTEM_PROMPT hook flag.
    """
    if not _prompt_transform_hooks_enabled() or hook_bus is None:
        return _maybe_apply_prompt_injection_sections(list(sections))

    from magi_agent.harness.resolved import (
        build_default_resolved_harness_state,
    )
    from magi_agent.hooks.context import HookContext
    from magi_agent.hooks.manifest import HookPoint

    state = harness_state or build_default_resolved_harness_state()

    # Project the current sections (+ model/provider/role) into the context the
    # hook receives. Preserve any caller-supplied HookContext via model_copy
    # (frozen models support update=); otherwise build a minimal one.
    section_tuple = tuple(sections)
    role_scope = "coding" if coding_agent else "general"
    projection: dict[str, object] = {"prompt_sections": section_tuple}
    if model:
        projection["agent_model"] = model
    if provider is not None:
        projection["provider_name"] = provider
    projection["policy_scope"] = role_scope

    if hook_context is not None and isinstance(hook_context, HookContext):
        context: object = hook_context.model_copy(update=projection)
    elif hook_context is not None:
        # Unknown context object (not a HookContext): we can't project
        # prompt_sections onto it, so fire with it unchanged (legacy fallback).
        # Unreachable from the two builders, which pass a HookContext or None.
        context = hook_context
    else:
        context = HookContext(
            bot_id="",
            prompt_sections=section_tuple,
            agent_model=model or None,
            provider_name=provider,
            policy_scope=role_scope,
        )

    try:
        run_result = hook_bus.run(
            point=HookPoint.BEFORE_SYSTEM_PROMPT,
            context=context,  # type: ignore[arg-type]
            harness_state=state,  # type: ignore[arg-type]
        )
    except Exception:  # fail-safe: never let a hook break prompt assembly
        logger.warning("promptTransform hook run failed; using original sections", exc_info=True)
        return _reassert_protected_sections(list(sections))

    transformed = list(sections)
    saw_replace = False
    if run_result.final_action == "replace":
        for result in run_result.results:
            if result.action != "replace":
                continue
            value = result.value
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                transformed = list(value)
                saw_replace = True
            else:
                logger.warning(
                    "promptTransform hook returned non-list[str] value; "
                    "failing safe to original sections"
                )

    # F-MUT1: append ``prompt_injection`` rule sections AFTER the HookBus
    # replace branch so a hook-authored full replacement composes
    # deterministically with rule-driven appends. The helper is a no-op when
    # the F-MUT1 master flag is OFF, preserving the byte-identical OFF
    # behavior.
    transformed = _maybe_apply_prompt_injection_sections(transformed)

    final_sections = _reassert_protected_sections(transformed)

    # Optional, default-off evidence emission. Only emits when the transform
    # actually RAN (flag on + bus present, reached here) AND a sink was provided.
    # ``hook_name`` is the tuple of effective hooks that ran at this point (the
    # bus exposes the per-result hook names only via this observation); a real
    # replace is signalled separately by ``sections_modified``.
    if evidence_sink is not None:
        tokens_before = _estimate_prompt_tokens(sections)
        tokens_after = _estimate_prompt_tokens(final_sections)
        payload: dict[str, object] = {
            "type": "PromptTransform",
            "hook_name": run_result.observation.effective_hooks,
            "sections_modified": saw_replace and final_sections != list(sections),
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        }
        try:
            evidence_sink(payload)
        except Exception:  # a sink error must never break prompt assembly
            logger.warning("promptTransform evidence sink raised; ignoring", exc_info=True)

    # PR-F-UX1 NOTE: The Tier 2 ``on_user_prompt_submit`` audit-only custom_rule
    # fan-out lives in :mod:`magi_agent.customize.lifecycle_audit` and is
    # invoked at the TOP of :func:`magi_agent.runtime.governed_turn.run_governed_turn`
    # — the canonical CLI/serve/child funnel — so the wire runs on real
    # production turns. This file's forbidden-import boundary
    # (``test_message_builder_source_forbids_live_runtime_side_effect_boundaries``)
    # stays intact because the audit module is never imported here. See
    # ``magi_agent/customize/lifecycle_audit.py`` for the audit semantics and
    # ``tests/customize_firing/test_user_prompt_submit_firing.py`` for the
    # end-to-end firing contract.

    return final_sections


def build_system_prompt(
    *,
    session_key: str,
    turn_id: str,
    identity: Mapping[str, object] | None = None,
    channel: Mapping[str, object] | object | None = None,
    user_message: Mapping[str, object] | object | None = None,
    now: datetime | None = None,
    timezone: str | tzinfo | None = None,
    coding_agent: bool = False,
    model: str = "",
    model_aware_prompts_enabled: bool = False,
    hook_bus: "HookBus | None" = None,
    harness_state: "ResolvedHarnessPresetState | None" = None,
    hook_context: "object | None" = None,
    evidence_sink: "Callable[[Mapping[str, object]], None] | None" = None,
    memory_snapshot_block: str = "",
    okf_guidance_block: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    runtime_now = _coerce_utc(now)
    static_parts, dynamic_parts = _assemble_prompt_sections(
        session_key=session_key,
        turn_id=turn_id,
        identity=identity,
        channel=channel,
        user_message=user_message,
        runtime_now=runtime_now,
        timezone=timezone,
        coding_agent=coding_agent,
        model=model,
        model_aware_prompts_enabled=model_aware_prompts_enabled,
        memory_snapshot_block=memory_snapshot_block,
        okf_guidance_block=okf_guidance_block,
        env=env,
    )

    static_parts = _apply_prompt_transform(
        static_parts,
        hook_bus=hook_bus,
        harness_state=harness_state,
        hook_context=hook_context,
        model=model,
        provider=None,  # build_system_prompt has no provider param
        coding_agent=coding_agent,
        evidence_sink=evidence_sink,
    )
    prompt_parts = [*static_parts, PROMPT_DYNAMIC_BOUNDARY, *dynamic_parts]
    return "\n\n".join(prompt_parts)


def build_system_prompt_blocks(
    *,
    session_key: str,
    turn_id: str,
    identity: Mapping[str, object] | None = None,
    channel: Mapping[str, object] | object | None = None,
    user_message: Mapping[str, object] | object | None = None,
    now: datetime | None = None,
    timezone: str | tzinfo | None = None,
    model: str = "",
    provider: str = "auto",
    cache_enabled: bool = False,
    coding_agent: bool = False,
    model_aware_prompts_enabled: bool = False,
    hook_bus: "HookBus | None" = None,
    harness_state: "ResolvedHarnessPresetState | None" = None,
    hook_context: "object | None" = None,
    evidence_sink: "Callable[[Mapping[str, object]], None] | None" = None,
    memory_snapshot_block: str = "",
    okf_guidance_block: str = "",
    env: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Build the system prompt as a list of structured blocks with optional cache markers.

    When *cache_enabled* is ``False`` (default), returns a single
    ``{"type": "text", "text": ...}`` dict whose text is identical to what
    :func:`build_system_prompt` returns — no cache markers are added.

    When *cache_enabled* is ``True``, the prompt is split into its constituent
    parts, each part becomes its own block, and static parts (rendered identity,
    ``DEFERRAL_PREVENTION_BLOCK``, ``OUTPUT_RULES_BLOCK``) receive
    provider-appropriate cache markers injected by
    :class:`~magi_agent.prompt.injection.CacheControlInjector`.

    Args:
        session_key: Opaque session identifier injected into the header.
        turn_id: Per-turn identifier injected into the header.
        identity: Identity sections mapping (same as :func:`build_system_prompt`).
        channel: Channel metadata object (same as :func:`build_system_prompt`).
        user_message: Current user message (same as :func:`build_system_prompt`).
        now: Optional UTC datetime for the temporal context.
        timezone: Optional timezone for local time display.
        model: Model identifier, used to auto-detect the provider when
            *provider* is ``"auto"``.
        provider: Provider hint (``"anthropic"``, ``"openai"``, ``"google"``,
            or ``"auto"``).  Defaults to ``"auto"`` which infers the provider
            from *model*.
        cache_enabled: When ``True``, split into multiple blocks and inject
            provider cache markers on static blocks.

    Returns:
        Ordered list of ``{"type": "text", "text": ..., "cache_control"?: ...}``
        dicts ready for inclusion in an Anthropic (or other provider) API
        request.  A list with a single item is returned when
        *cache_enabled* is ``False``.
    """
    # Single assembly path (rule 6): both cache modes build sections via the
    # shared helper and fire the transform hook EXACTLY ONCE here, rather than
    # delegating to build_system_prompt() (which would either skip the hook on
    # the non-cache branch or double-apply it).
    #
    # Track-12 cache note: _apply_prompt_transform runs over the STATIC sections
    # below, which are Track-12's cacheable byte-identical prefix. A hook that
    # replaces sections (flag ON) mutates that prefix and LOWERS the prompt-cache
    # hit rate; when the flag is OFF the transform short-circuits and the prefix
    # stays byte-identical, so there is no cache regression.
    runtime_now = _coerce_utc(now)
    # PR10 cache note: when model_aware_prompts_enabled is ON, the per-family
    # coding hint is added to the STATIC region. This keeps the hint cacheable,
    # but it SEGMENTS the prompt cache by provider family — one cache prefix per
    # family (openai / google / fireworks / default) rather than a single shared
    # prefix. The hint stays in the static region so the rest of the prefix
    # remains byte-identical and cacheable within each family. When the flag is
    # OFF, model is ignored and the prefix is model-independent (no segmentation).
    static_parts, dynamic_parts = _assemble_prompt_sections(
        session_key=session_key,
        turn_id=turn_id,
        identity=identity,
        channel=channel,
        user_message=user_message,
        runtime_now=runtime_now,
        timezone=timezone,
        coding_agent=coding_agent,
        model=model if model_aware_prompts_enabled else "",
        model_aware_prompts_enabled=model_aware_prompts_enabled,
        memory_snapshot_block=memory_snapshot_block,
        okf_guidance_block=okf_guidance_block,
        env=env,
    )
    static_parts = _apply_prompt_transform(
        static_parts,
        hook_bus=hook_bus,
        harness_state=harness_state,
        hook_context=hook_context,
        model=model,
        provider=provider,
        coding_agent=coding_agent,
        evidence_sink=evidence_sink,
    )

    if not cache_enabled:
        text = "\n\n".join([*static_parts, PROMPT_DYNAMIC_BOUNDARY, *dynamic_parts])
        return [{"type": "text", "text": text}]

    parts = [*static_parts, PROMPT_DYNAMIC_BOUNDARY, *dynamic_parts]
    static_indices = frozenset(range(len(static_parts)))

    from magi_agent.prompt.splitter import split_system_prompt
    from magi_agent.prompt.injection import CacheControlInjector

    split_result = split_system_prompt(parts, static_indices)
    injector = CacheControlInjector(provider=provider, model=model)
    return injector.inject(split_result.blocks)


def build_current_user_message(
    user_message: Mapping[str, object] | object,
    *,
    workspace_root: str | None = None,
) -> dict[str, object]:
    text = str(_field(user_message, "text", default=""))
    metadata = _field(user_message, "metadata", default=None)
    reply_to = _field(metadata, "replyTo", "reply_to", default=None)
    base_user_content = (
        f"{format_reply_preamble(reply_to)}\n{text}" if reply_to is not None else text
    )
    attachment_preamble = _format_attachments_preamble(
        _field(user_message, "attachments", default=None),
        workspace_root,
    )
    user_content = "\n\n".join(
        part for part in (base_user_content, attachment_preamble) if part
    )
    image_blocks = _collect_image_blocks(user_message, metadata)
    if image_blocks:
        content: list[object] = []
        if user_content:
            content.append({"type": "text", "text": user_content})
        content.extend(image_blocks)
        return {"role": "user", "content": content}
    return {"role": "user", "content": user_content}


def append_runtime_model_identity_context(
    messages: MutableSequence[dict[str, Any]],
    *,
    configured_model: str,
    effective_model: str,
    route_decision: Mapping[str, object] | object | None = None,
) -> None:
    _remove_runtime_model_identity_context(messages)
    identity_block = {
        "type": "text",
        "text": _build_runtime_model_identity_text(
            configured_model=configured_model,
            effective_model=effective_model,
            route_decision=route_decision,
        ),
    }
    last = messages[-1] if messages else None
    if _begins_with_tool_result(last):
        last_content = last["content"]
        if isinstance(last_content, list):
            last_content.append(identity_block)
            return

    identity_message = {"role": "user", "content": [identity_block]}
    insert_at = max(0, len(messages) - 1)
    while insert_at > 0:
        before = messages[insert_at - 1]
        after = messages[insert_at]
        if (
            before.get("role") == "assistant"
            and after.get("role") == "user"
            and _has_tool_use_block(before)
            and _has_tool_result_block(after)
        ):
            insert_at -= 1
            continue
        break
    messages.insert(insert_at, identity_message)


def token_limit_for_compaction(
    *,
    configured_model: str,
    effective_model: str | None = None,
    context_window: int | None = None,
    model_context_windows: Mapping[str, int] | None = None,
) -> int:
    model = effective_model or configured_model
    if isinstance(context_window, int) and context_window > 0:
        return math.floor(context_window * 0.75)
    if model_context_windows:
        model_context_window = model_context_windows.get(model)
        if isinstance(model_context_window, int) and model_context_window > 0:
            return math.floor(model_context_window * 0.75)
    from magi_agent.context._token_window_table import (  # noqa: PLC0415
        _KNOWN_TOKEN_LIMITS,
    )

    known_limit = _KNOWN_TOKEN_LIMITS.get(model)
    if known_limit is not None:
        return known_limit
    if model.startswith(_OPENAI_COMPAT_MODEL_PREFIXES):
        return math.floor(_OPENAI_COMPAT_CONTEXT_WINDOW * 0.75)
    return TOKEN_LIMIT_FOR_COMPACTION


def _coerce_utc(now: datetime | None) -> datetime:
    value = now if isinstance(now, datetime) else datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _isoformat_z(value: datetime) -> str:
    utc_value = _coerce_utc(value)
    milliseconds = math.floor(utc_value.microsecond / 1000)
    return f"{utc_value:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z"


def _resolve_timezone(timezone: str | tzinfo | None) -> tuple[tzinfo, str]:
    if isinstance(timezone, tzinfo):
        name = getattr(timezone, "key", None)
        return timezone, name if isinstance(name, str) else timezone.tzname(None) or "UTC"
    if isinstance(timezone, str) and timezone.strip():
        name = timezone.strip()
        try:
            return ZoneInfo(name), name
        except ZoneInfoNotFoundError:
            return UTC, "UTC"
    return UTC, "UTC"


def _field(source: object, *names: str, default: object = None) -> object:
    if source is None:
        return default
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return default
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return default


def _channel_type(channel: Mapping[str, object] | object | None) -> str:
    raw = _field(channel, "type", default=None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "web"


def _memory_mode_block(channel: Mapping[str, object] | object | None) -> str:
    raw = _field(channel, "memoryMode", "memory_mode", default=None)
    memory_mode = raw.strip().lower() if isinstance(raw, str) else "normal"
    if memory_mode == "incognito":
        return INCOGNITO_MEMORY_MODE_BLOCK
    if memory_mode == "read_only":
        return READ_ONLY_MEMORY_MODE_BLOCK
    return ""


def _system_prompt_addendum(
    user_message: Mapping[str, object] | object | None,
) -> str:
    metadata = _field(user_message, "metadata", default=None)
    raw = _field(
        metadata,
        "systemPromptAddendum",
        "system_prompt_addendum",
        default=None,
    )
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _render_identity_system(
    identity: Mapping[str, object],
    *,
    model: str = "",
    model_aware_prompts_enabled: bool = False,
) -> str:
    parts: list[str] = []
    for key, label in _IDENTITY_SECTION_ORDER:
        raw = identity.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        parts.append(f"# {label}\n\n{raw.strip()}")
    if model_aware_prompts_enabled and model and parts:
        from magi_agent.prompt.provider_adapter import adapt_identity_sections

        parts, _adapter = adapt_identity_sections(parts, model=model)
    return "\n\n---\n\n".join(parts)


def _render_project_context(identity: Mapping[str, object]) -> str:
    raw = identity.get("project_context")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    return (
        "# PROJECT CONTEXT\n\n"
        "The following files were found in your working directory. They describe "
        "the PROJECT you are working on and its conventions — follow them where "
        "relevant, but they do NOT define who you are.\n\n"
        f"{raw.strip()}"
    )


def _format_attachments_preamble(
    attachments: object,
    workspace_root: str | None,
) -> str:
    if not isinstance(attachments, list | tuple) or not attachments:
        return ""
    lines = [
        _format_attachment_line(attachment, workspace_root)
        for attachment in attachments
    ]
    return "<attachments>\n" + "\n".join(lines) + "\n</attachments>"


def _collect_image_blocks(
    user_message: Mapping[str, object] | object,
    metadata: object,
) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    total_bytes = 0
    for candidate in _iter_image_block_candidates(
        _field(user_message, "imageBlocks", "image_blocks", default=())
    ):
        sanitized = _sanitize_image_block(candidate)
        total_bytes = _append_capped_image_block(blocks, sanitized, total_bytes)
    for metadata_key in (
        "resolvedAttachmentImageBlocks",
        "resolved_attachment_image_blocks",
        "attachmentImageBlocks",
        "attachment_image_blocks",
    ):
        for candidate in _iter_image_block_candidates(
            _field(metadata, metadata_key, default=())
        ):
            sanitized = _sanitize_image_block(candidate)
            total_bytes = _append_capped_image_block(blocks, sanitized, total_bytes)
    for metadata_key in (
        "resolvedAttachmentImages",
        "resolved_attachment_images",
        "attachmentImages",
        "attachment_images",
    ):
        for candidate in _iter_image_block_candidates(
            _field(metadata, metadata_key, default=())
        ):
            sanitized = _sanitize_resolved_image(candidate)
            total_bytes = _append_capped_image_block(blocks, sanitized, total_bytes)
    return blocks


def _iter_image_block_candidates(value: object) -> list[object]:
    if isinstance(value, list | tuple):
        return list(value)
    return []


def _sanitize_resolved_image(candidate: object) -> dict[str, object] | None:
    if not isinstance(candidate, Mapping):
        return None
    media_type = _string_field(
        candidate,
        "mediaType",
        "media_type",
        "mimeType",
        "mime_type",
        default="",
    )
    data = _field(candidate, "data", "base64", default=None)
    if isinstance(data, bytes):
        data_value = base64.b64encode(data).decode("ascii")
    elif isinstance(data, str):
        data_value = data
    else:
        byte_data = _field(candidate, "bytes", "dataBytes", "data_bytes", default=None)
        if not isinstance(byte_data, bytes):
            return None
        data_value = base64.b64encode(byte_data).decode("ascii")
    return _sanitize_image_block(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data_value,
            },
        }
    )


def _sanitize_image_block(block: object) -> dict[str, object] | None:
    if not isinstance(block, Mapping) or block.get("type") != "image":
        return None
    source = block.get("source")
    if not isinstance(source, Mapping) or source.get("type") != "base64":
        return None
    media_type = _string_field(
        source,
        "media_type",
        "mediaType",
        default="",
    ).lower()
    if media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
        return None
    data = _string_field(source, "data", default="").strip()
    if not _is_valid_capped_base64(data):
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _append_capped_image_block(
    blocks: list[dict[str, object]],
    sanitized: dict[str, object] | None,
    total_bytes: int,
) -> int:
    if sanitized is None or len(blocks) >= MAX_IMAGE_BLOCK_COUNT:
        return total_bytes
    source = _field(sanitized, "source", default=None)
    data = _string_field(source, "data", default="")
    decoded_len = _decoded_base64_length(data)
    if decoded_len is None:
        return total_bytes
    next_total = total_bytes + decoded_len
    if next_total > MAX_IMAGE_BLOCK_TOTAL_BYTES:
        return total_bytes
    blocks.append(sanitized)
    return next_total


def _is_valid_capped_base64(data: str) -> bool:
    if not data:
        return False
    max_encoded_len = math.ceil(MAX_IMAGE_BLOCK_BYTES / 3) * 4 + 4
    if len(data) > max_encoded_len:
        return False
    decoded_len = _decoded_base64_length(data)
    return decoded_len is not None and 0 < decoded_len <= MAX_IMAGE_BLOCK_BYTES


def _decoded_base64_length(data: str) -> int | None:
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    return len(decoded)


def _format_attachment_line(attachment: object, workspace_root: str | None) -> str:
    kind = _escape_display_field(
        _string_field(attachment, "kind", default="file"),
        default="file",
    )
    name = _escape_display_field(
        _string_field(attachment, "name", default="attachment"),
        default="attachment",
    )
    mime_type = _escape_display_field(
        _string_field(attachment, "mimeType", "mime_type", default=""),
        default="",
    )
    size_bytes = _field(attachment, "sizeBytes", "size_bytes", default=None)
    mime = ""
    if mime_type:
        suffix = f", {size_bytes} bytes" if isinstance(size_bytes, int) else ""
        mime = f" ({mime_type}{suffix})"
    workspace_path = _workspace_path_for_attachment(attachment, workspace_root)
    location = (
        f" workspace_path={_escape_display_field(workspace_path, default='')}"
        if workspace_path
        else ""
    )
    return f"- {kind}: {name}{mime}{location}"


def _escape_display_field(value: str, *, default: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    redacted = _public_sanitize_attachment_display(collapsed)
    if len(redacted) > ATTACHMENT_DISPLAY_MAX_CHARS:
        redacted = f"{redacted[:ATTACHMENT_DISPLAY_MAX_CHARS]}…"
    escaped = html.escape(redacted, quote=True)
    return escaped if escaped else default


def _public_sanitize_attachment_display(value: str) -> str:
    sanitized = _URL_PATTERN.sub("[REDACTED_URL]", value)
    sanitized = _AUTH_HEADER_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _BASIC_AUTH_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _SIGNED_QUERY_PARAM_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _CREDENTIAL_FIELD_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _TELEGRAM_BOT_TOKEN_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _COMMON_SECRET_TOKEN_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _PRIVATE_PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    return re.sub(r"\s+", " ", sanitized).strip()


def _string_field(source: object, *names: str, default: str) -> str:
    value = _field(source, *names, default=default)
    return value if isinstance(value, str) else default


def _workspace_path_for_attachment(
    attachment: object,
    workspace_root: str | None,
) -> str | None:
    local_path = _string_field(attachment, "localPath", "local_path", default="")
    if not local_path or not workspace_root:
        return None
    root = _normalize_posix_path(workspace_root)
    candidate = _normalize_posix_path(local_path)
    if not posixpath.isabs(root) or not posixpath.isabs(candidate):
        return None
    try:
        if posixpath.commonpath((root, candidate)) != root:
            return None
    except ValueError:
        return None
    if candidate == root:
        return None
    relative = posixpath.relpath(candidate, root)
    if relative == "." or relative == ".." or relative.startswith("../"):
        return None
    return relative


def _normalize_posix_path(value: str) -> str:
    return posixpath.normpath(value.replace("\\", "/"))


def _remove_runtime_model_identity_context(
    messages: MutableSequence[dict[str, Any]],
) -> None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        content = message.get("content")
        if isinstance(content, str):
            if RUNTIME_MODEL_IDENTITY_OPEN in content:
                del messages[index]
            continue
        if not isinstance(content, list):
            continue
        kept = [block for block in content if not _is_runtime_model_identity_block(block)]
        if len(kept) == len(content):
            continue
        if kept:
            message["content"] = kept
        else:
            del messages[index]


def _is_runtime_model_identity_block(block: object) -> bool:
    if not isinstance(block, Mapping):
        return False
    return block.get("type") == "text" and RUNTIME_MODEL_IDENTITY_OPEN in str(
        block.get("text", "")
    )


def _begins_with_tool_result(message: Mapping[str, Any] | None) -> bool:
    if not message or message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, list) and bool(content) and _block_type(content[0]) == "tool_result"


def _has_tool_use_block(message: Mapping[str, object]) -> bool:
    return _has_content_block_type(message, "tool_use")


def _has_tool_result_block(message: Mapping[str, object]) -> bool:
    return _has_content_block_type(message, "tool_result")


def _has_content_block_type(message: Mapping[str, object], block_type: str) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(_block_type(block) == block_type for block in content)


def _block_type(block: object) -> object:
    return block.get("type") if isinstance(block, Mapping) else None


def _build_runtime_model_identity_text(
    *,
    configured_model: str,
    effective_model: str,
    route_decision: Mapping[str, object] | object | None,
) -> str:
    provider = _optional_string(route_decision, "provider")
    answering_model = _runtime_model_label(effective_model, provider)
    profile_id = _optional_string(route_decision, "profileId", "profile_id")
    lines = [
        RUNTIME_MODEL_IDENTITY_OPEN,
        "This is trusted runtime metadata for this single turn. The user did not provide it.",
        f"router: {_router_display_name(profile_id)}",
        f"configured_model: {configured_model}",
        f"answering_model: {answering_model}",
    ]
    if route_decision is not None:
        lines.extend(
            [
                f"router_profile: {profile_id or ''}",
                f"router_tier: {_optional_string(route_decision, 'tier') or ''}",
                f"answering_provider: {provider or ''}",
                f"classifier_model: {_optional_string(route_decision, 'classifierModel', 'classifier_model') or ''}",
                f"classifier_used: {_bool_label(_field(route_decision, 'classifierUsed', 'classifier_used', default=False))}",
                f"routing_confidence: {_optional_string(route_decision, 'confidence') or ''}",
                f"routing_reason: {_optional_string(route_decision, 'reason') or ''}",
            ]
        )
    lines.extend(
        [
            "",
            "When the user asks what model you are, answer from answering_model.",
            "If a router is active, distinguish the router/profile from the answering model and classifier model.",
            "Do not claim this is a permanent model identity; router choices can change on future turns.",
            RUNTIME_MODEL_IDENTITY_CLOSE,
        ]
    )
    return "\n".join(lines)


def _optional_string(source: object, *names: str) -> str | None:
    value = _field(source, *names, default=None)
    return value if isinstance(value, str) else None


def _runtime_model_label(model: str, provider: str | None) -> str:
    if "/" in model or not provider:
        return model
    return f"{provider}/{model}"


def _router_display_name(profile_id: str | None) -> str:
    if profile_id == "standard":
        return "Standard Router"
    if profile_id == "premium":
        return "Premium Router"
    if profile_id == "anthropic_only":
        return "Claude Router"
    return f"{profile_id} Router" if profile_id else "Direct model"


def _bool_label(value: object) -> str:
    return "true" if value is True else "false"
