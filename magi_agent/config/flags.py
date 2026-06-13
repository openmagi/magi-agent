"""Canonical feature-flag registry + typed reader (single source of truth).

This module is the PR2 foundation of the flag-governance cluster
(``docs/plans/2026-06-09-magi-oss-full-activation/15-flag-governance.md``).

Goal
----
Today ~154 ``MAGI_*``/``CORE_AGENT_*`` flags are read with ad-hoc
``os.environ.get(...).strip().lower()`` call sites scattered across ~114 files,
and the truthy set is re-implemented in 26 places. There is no machine-readable
inventory, so ``docs/env-reference.md`` drifts (9.7% coverage) and typos
(``MAGI_X_ENABED``) silently fall back to defaults.

``flags.py`` provides:

* ``FlagSpec`` — a frozen dataclass describing one flag (name / default / scope /
  stage / summary / kind).
* ``FLAGS`` — the single registry: a ``tuple[FlagSpec, ...]``. New flags register
  here in one line. This is the machine-readable source for the later
  env-reference generator (PR4) and the stage table (PR5).
* ``flag_bool`` / ``flag_str`` / ``flag_int`` — typed readers that pull the
  registered default and parse consistently, reusing the existing ``env.py``
  truthy convention.

Scope of *this* PR (PR2): infrastructure only. The ~154 raw call sites are NOT
migrated here (PR3), and the env-reference / gates-table generators are NOT
written here (PR4/PR5). A few already-standardized ``env.py`` ``is_*_enabled``
helpers are re-implemented as thin delegations to keep behaviour
byte-identical and prove the seam.

Two distinct boolean semantics
------------------------------
There are two real boolean shapes in the codebase and the registry models them
as *separate kinds* so PR4/PR5 docs do not misrepresent them:

* ``kind="bool"`` — strict opt-in (``"1"/"true"/"yes"/"on"`` truthy, everything
  else — including unset — falsey, falling back to the registry ``default``).
  Read with :func:`flag_bool`.
* ``kind="profile_bool"`` — *profile-aware default-ON*, mirroring
  ``env._runtime_feature_enabled``: unset (or an unrecognised value) resolves to
  ON in the full runtime profile and OFF under ``MAGI_RUNTIME_PROFILE`` in
  ``safe``/``eval``/etc.; an explicit ``"0"/"false"/...`` always wins. Read with
  :func:`flag_profile_bool`. These flags carry **no** flat ``default`` truthy
  value because their default is not a constant — it is a function of the
  runtime profile. Modelling them as a plain ``default=False`` (or even
  ``default=True``) ``bool`` would silently flatten the profile dimension and
  mislead operators about when the gate is actually on (the spec's explicit
  "별 kind로 보존, 단순 truthy로 뭉개지 말 것" rule, 15-flag-governance.md §3.2 / PR2 risk).

:func:`flag_bool` and :func:`flag_profile_bool` each reject the other kind so a
profile-aware gate can never be silently read through the strict-truthy path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Literal

# Reuse the single truthy convention already established in env.py rather than
# inventing a new one (the cluster's explicit "no new truthy set" rule). The
# profile-aware reader delegates to env's _runtime_feature_enabled so there is
# exactly one source of truth for the profile-default-ON resolution.
from .env import _is_true, _runtime_feature_enabled

__all__ = [
    "FlagScope",
    "Stage",
    "FlagKind",
    "FlagSpec",
    "FLAGS",
    "FLAGS_BY_NAME",
    "get_flag",
    "flag_bool",
    "flag_profile_bool",
    "flag_str",
    "flag_int",
]


FlagScope = Literal["public", "hosted", "internal", "dev"]
"""Audience of a flag.

* ``public``   — self-host operator toggles (``MAGI_*``); exposed in env-reference.
* ``hosted``   — hosted-deploy only (``CORE_AGENT_*``); excluded from the public
  reference.
* ``internal`` — internal wiring / non-operator surface.
* ``dev``      — test / debug only.
"""

Stage = Literal["stage1", "stage2", "stage3"]
"""Maximum stage a seam can safely reach (see ``docs/default-off-gates.md``).

* ``stage1`` — Built/Observe: code exists, default-OFF, observe-only.
* ``stage2`` — Local/Gated: enabling changes behaviour at local/single-user scope.
* ``stage3`` — Authority/Traffic: authority attached to hosted live traffic.
"""

FlagKind = Literal["bool", "profile_bool", "str", "int"]
"""Reader semantics of a flag.

* ``bool``         — strict-truthy opt-in with a flat registry ``default``.
* ``profile_bool`` — profile-aware default-ON (``env._runtime_feature_enabled``):
  unset resolves ON in the full profile, OFF under safe/eval profiles; explicit
  values win. Carries ``default=None`` (its default is not a constant).
* ``str`` / ``int`` — typed value flags.
"""


@dataclass(frozen=True)
class FlagSpec:
    """Single registry entry describing one environment flag."""

    name: str
    default: str | bool | int | None
    scope: FlagScope
    stage: Stage
    summary: str
    kind: FlagKind = "bool"


def _b(
    name: str,
    *,
    default: bool = False,
    scope: FlagScope = "public",
    stage: Stage = "stage1",
    summary: str,
) -> FlagSpec:
    return FlagSpec(
        name=name, default=default, scope=scope, stage=stage, summary=summary, kind="bool"
    )


def _pb(
    name: str,
    *,
    scope: FlagScope = "public",
    stage: Stage = "stage1",
    summary: str,
) -> FlagSpec:
    """Register a *profile-aware default-ON* flag (``env._runtime_feature_enabled``).

    No flat ``default`` is taken because the default is a function of
    ``MAGI_RUNTIME_PROFILE`` (ON in the full profile, OFF under safe/eval), not a
    constant. ``default=None`` records "profile-resolved" for the generators.
    """

    return FlagSpec(
        name=name,
        default=None,
        scope=scope,
        stage=stage,
        summary=summary,
        kind="profile_bool",
    )


# ---------------------------------------------------------------------------
# FLAGS registry — single source of truth.
#
# PR2 scope: the operator-relevant *public* subset (high-value master switches
# and already-standardized ``is_*_enabled`` seams). Exhaustive registration of
# all ~279 MAGI_ flags is deferred to PR3 (call-site migration). New flags add a
# single line here; keep entries one-per-line and alphabetical within a group.
# ---------------------------------------------------------------------------
FLAGS: tuple[FlagSpec, ...] = (
    # --- Memory subsystem ---------------------------------------------------
    _b(
        "MAGI_MEMORY_ENABLED",
        summary="Master switch for the agent memory subsystem (3-tier + compaction).",
    ),
    _b(
        "MAGI_MEMORY_WRITE_ENABLED",
        summary="Allow the memory subsystem to persist writes (vs read-only recall).",
    ),
    _b(
        "MAGI_MEMORY_RECALL_ENABLED",
        summary="Enable memory recall/injection into the working context.",
    ),
    _b(
        "MAGI_MEMORY_COMPACTION_ENABLED",
        summary="Enable the 5-level compaction tree builder for stored memory.",
    ),
    _b(
        "MAGI_MEMORY_QMD_LIVE_ENABLED",
        summary="Use the live qmd search backend for memory recall.",
    ),
    _b(
        "MAGI_MEMORY_MODE_ROUTING_ENABLED",
        summary="Honour the per-channel memory mode header (normal/read-only/incognito).",
    ),
    _b(
        "MAGI_MEMORY_PROJECTION_ENABLED",
        summary="Project a lean memory view into the serve prompt block.",
    ),
    # --- Learning / self-improvement ---------------------------------------
    _b(
        "MAGI_LEARNING_ENABLED",
        summary="Master switch for the learned-skills / self-improvement loop.",
    ),
    _b(
        "MAGI_LEARNING_LIVE_ENABLED",
        summary="Allow the learning loop to run with live model-backed proposers.",
    ),
    _b(
        "MAGI_LEARNING_INJECTION_ENABLED",
        summary="Inject learned skills/refinements into the runtime prompt.",
    ),
    _b(
        "MAGI_LEARNING_REFLECTION_ENABLED",
        summary="Enable post-turn reflection that feeds the learning loop.",
    ),
    # --- Channels / always-on ----------------------------------------------
    _b(
        "MAGI_GOAL_LOOP_ENABLED",
        summary="Enable the autonomous goal-loop scheduler.",
    ),
    _b(
        "MAGI_OBSERVABILITY_ENABLED",
        summary="Enable the hook-tap observability module (bot-activity visibility).",
    ),
    # --- Web / research tools ----------------------------------------------
    _b(
        "MAGI_DEEP_WEB_RESEARCH_ENABLED",
        summary="Enable the live deep web-research harness (search + fetch + verify).",
    ),
    FlagSpec(
        name="MAGI_RESEARCH_GOVERNANCE_MODE",
        default="off",
        scope="public",
        stage="stage1",
        summary=(
            "Research governance mode. `off` is inert; `audit` records "
            "source/citation mismatches without blocking."
        ),
        kind="str",
    ),
    _b(
        "MAGI_RESEARCH_FACT_GUIDANCE_ENABLED",
        summary=(
            "Enable research_fact cross-check guidance: consolidated brief "
            "header/footer plus the <web_research> system-prompt block "
            "(requires BRAVE_API_KEY + FIRECRAWL_API_KEY)."
        ),
    ),
    _b(
        "MAGI_BROWSER_TOOL_ENABLED",
        summary="Expose the browser-use autonomous vision BrowserTask tool.",
    ),
    _b(
        "MAGI_CODE_ACTION_ENABLED",
        summary="Expose the persistent PythonExec code-execution tool.",
    ),
    FlagSpec(
        name="MAGI_CODE_ACTION_TIMEOUT_MS",
        default=30_000,
        scope="public",
        stage="stage1",
        summary=(
            "Per-call wall-clock timeout (ms) for the PythonExec tool; "
            "clamped to 1000-120000."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_CODE_ACTION_MAX_OUTPUT_BYTES",
        default=8_192,
        scope="public",
        stage="stage1",
        summary=(
            "Head+tail output cap per stream (bytes) for PythonExec results; "
            "clamped to 1024-65536."
        ),
        kind="int",
    ),
    _b(
        "MAGI_FILE_DELIVERY_LIVE_ENABLED",
        summary="Enable the live file-delivery tool (vs receipt-only).",
    ),
    _b(
        "MAGI_DOCUMENT_QA_ENABLED",
        summary=(
            "Expose the question-conditioned DocumentQA file-QA sidecar tool "
            "(requires MAGI_FILE_TOOLS_ENABLED); strict default-OFF in all profiles."
        ),
    ),
    FlagSpec(
        name="MAGI_DOCUMENT_QA_MODEL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Model id override for the DocumentQA sidecar call (e.g. a cheap "
            "haiku-class model); unset uses the configured provider model."
        ),
        kind="str",
    ),
    _b(
        "MAGI_CROSS_VERIFY_ENABLED",
        summary="Enable the cross-verification gate over spawned-agent results.",
    ),
    _b(
        "MAGI_DEFERRED_TOOLS_ENABLED",
        summary="Enable deferred (lazily-loaded) tool schemas.",
    ),
    _b(
        "MAGI_HEADTAIL_TRUNCATION_ENABLED",
        summary=(
            "Use head+tail (middle-elision) truncation for tool output caps "
            "instead of head-only, so document/page tails stay visible."
        ),
    ),
    # --- Vision sidecar (string overrides) -----------------------------------
    FlagSpec(
        name="MAGI_VISION_MODEL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Vision-sidecar model override for image_understand (bare model id, "
            "same semantics as MAGI_MODEL); unset keeps the main provider/model."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_VISION_PROVIDER",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Optional provider for MAGI_VISION_MODEL (anthropic|openai|gemini|"
            "fireworks); unset inherits the main provider's credentials."
        ),
        kind="str",
    ),
    # --- Coding harness -----------------------------------------------------
    # Profile-aware default-ON (env._runtime_feature_enabled): ON in the full
    # runtime profile, OFF under MAGI_RUNTIME_PROFILE=safe|eval.
    _pb(
        "MAGI_EDIT_FUZZY_MATCH_ENABLED",
        summary="Use the 9-stage fuzzy-match cascade for FileEdit (default-ON full profile).",
    ),
    _pb(
        "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED",
        summary="Run a formatter on files written by the coding harness (default-ON full profile).",
    ),
    _b(
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        summary="Reflect on failed edits before retrying (coding repair loop).",
    ),
    _b(
        "MAGI_CODING_REPAIR_LOOP_ENABLED",
        summary="Enable the iterative coding repair loop on failing edits.",
    ),
    _pb(
        "MAGI_LSP_DIAGNOSTICS_ENABLED",
        summary="Surface LSP diagnostics to the coding harness (default-ON full profile).",
    ),
    _pb(
        "MAGI_RIPGREP_ENABLED",
        summary="Use ripgrep for fast in-repo search when available (default-ON full profile).",
    ),
    _pb(
        "MAGI_APPLY_PATCH_ENABLED",
        summary="Enable the apply-patch tool for multi-file edits (default-ON full profile).",
    ),
    _b(
        "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED",
        summary=(
            "Live-SWE-style tool-synthesis: per-step reflection nudge + "
            "'create your own tools' recipe block (frontier-tier models only)."
        ),
    ),
    _b(
        "MAGI_COMPUTE_VIA_CODE_ENABLED",
        summary=(
            "Append a general prompt directive to compute arithmetic, conversions, "
            "statistics, and checksums by running code instead of mental math."
        ),
    ),
    _b(
        "MAGI_FORMAT_ADHERENCE_ENABLED",
        summary=(
            "Append a general prompt self-check for exact requested units, scale, "
            "rounding precision, names, and answer format."
        ),
    ),
    _b(
        "MAGI_MULTI_FILE_JOIN_ENABLED",
        summary=(
            "Append multi-file cross-reference guidance: enumerate archives, "
            "read structured files fully, and run joins/dedup programmatically."
        ),
    ),
    # --- Evidence / verification gates -------------------------------------
    _b(
        "MAGI_EGRESS_GATE_ENABLED",
        summary="Run the evidence-grounded critic gate before chat egress.",
    ),
    _b(
        "MAGI_STEP_DECOMPOSITION_ENABLED",
        summary=(
            "Inject a light first-pass guidance asking the agent to enumerate "
            "dependent sub-steps up front and confirm each before proceeding "
            "(prompt-only nudge; reuses existing planning seams)."
        ),
    ),
    _b(
        "MAGI_GA_DELIVERABLE_GATE_ENABLED",
        stage="stage2",
        summary=(
            "Enable the GA artifact-deliverable pre-final gate; strict "
            "default-OFF and inert unless explicitly set."
        ),
    ),
    _b(
        "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED",
        stage="stage2",
        summary=(
            "Run semantic grounding verification on the live evidence gate: a "
            "research answer asserting a specific numeric/identifier value not "
            "present in the opened-source corpus stays ungrounded and blocks. "
            "Strict default-OFF and inert unless explicitly set."
        ),
    ),
    _pb(
        "MAGI_SELF_INTROSPECTION_ENABLED",
        summary="Advertise the InspectSelfEvidence tool (default-ON full profile).",
    ),
    _pb(
        "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED",
        summary="Build per-turn EvidenceLedger objects (default-ON full profile).",
    ),
    FlagSpec(
        name="MAGI_EVIDENCE_LEDGER_DIR",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Directory for opt-in durable per-session JSONL evidence ledgers; "
            "unset keeps the lean in-memory live view only."
        ),
        kind="str",
    ),
    _pb(
        "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED",
        summary="Block turn completion when required evidence is missing (default-ON full profile).",
    ),
    _b(
        "MAGI_DOCUMENT_AUTHORING_COVERAGE",
        summary="Block document turns on failed DocumentCoverage (vs audit-only).",
        scope="public",
    ),
    # --- Resilience controls ------------------------------------------------
    # Profile-aware default-ON (env._runtime_feature_enabled): ON in the full
    # runtime profile, OFF under MAGI_RUNTIME_PROFILE=safe|eval.
    _pb(
        "MAGI_LOOP_GUARD_ENABLED",
        summary="Enable the repetition/loop guard brake (default-ON full profile).",
    ),
    _pb(
        "MAGI_ERROR_RECOVERY_ENABLED",
        summary="Enable automatic error-recovery retries (default-ON full profile).",
    ),
    _pb(
        "MAGI_OUTPUT_CONTINUATION_ENABLED",
        summary="Enable automatic continuation of truncated model output (default-ON full profile).",
    ),
    _pb(
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        summary="Compact the working context when the token threshold is hit (default-ON full profile).",
    ),
    # --- In-context replanning ----------------------------------------------
    # Strict default-OFF (flat _b, NOT profile-resolved): MAGI_RUNTIME_PROFILE
    # never auto-enables the facts-survey injection.
    _b(
        "MAGI_FACTS_REPLAN_ENABLED",
        stage="stage2",
        summary=(
            "Inject a periodic in-context facts survey (given/learned/look-up/"
            "derive) + plan refresh into the live model loop every N working steps."
        ),
    ),
    FlagSpec(
        name="MAGI_FACTS_REPLAN_INTERVAL",
        default=4,
        scope="public",
        stage="stage2",
        summary=(
            "Working steps between facts surveys (>= 1; a non-positive value "
            "disables the control)."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_FACTS_REPLAN_MAX_PER_TURN",
        default=5,
        scope="public",
        stage="stage2",
        summary=(
            "Hard cap on facts surveys injected per (session, turn) (>= 1; a "
            "non-positive value disables the control)."
        ),
        kind="int",
    ),
    # --- Surfaces -----------------------------------------------------------
    # NOTE: flat default-ON, NOT profile-aware. cli/headless.py:_cli_enabled
    # reads MAGI_CLI_ENABLED directly (unset => True regardless of
    # MAGI_RUNTIME_PROFILE); it does not consult _runtime_feature_enabled, so a
    # plain default=True bool is the faithful model here (kept as kind="bool").
    _b(
        "MAGI_CLI_ENABLED",
        default=True,
        summary="Enable the magi CLI surface (headless NDJSON + Textual TUI); flat default-ON.",
    ),
    _b(
        "MAGI_HOSTED_STREAMING_SERVE",
        scope="hosted",
        summary=(
            "Serve hosted selected-gate5b chat over the SSE stream route with "
            "completions-equivalent gates (no local-engine fallthrough)."
        ),
    ),
    _b(
        "MAGI_HOSTED_SESSION_REUSE",
        scope="hosted",
        summary=(
            "Reuse the in-memory ADK session service across hosted turns keyed by "
            "(bot digest, session id); OFF keeps the fresh-per-turn behavior."
        ),
    ),
    FlagSpec(
        name="MAGI_HOSTED_SESSION_REUSE_MAX_ENTRIES",
        default=64,
        scope="hosted",
        stage="stage1",
        summary="LRU capacity (distinct sessions) of the hosted session-reuse registry.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_HOSTED_SESSION_REUSE_TTL_SECONDS",
        default=1800,
        scope="hosted",
        stage="stage1",
        summary="Idle TTL in seconds before a reusable hosted session is evicted.",
        kind="int",
    ),
    # --- Runtime profile (string) ------------------------------------------
    FlagSpec(
        name="MAGI_RUNTIME_PROFILE",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Runtime profile selector (safe/off/minimal/conservative/eval). "
            "Safe profiles disable default-ON resilience seams."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_CONFIG",
        default="",
        scope="public",
        stage="stage2",
        summary="Path to the Magi config.toml file; empty uses ~/.magi/config.toml.",
        kind="str",
    ),
)


FLAGS_BY_NAME: dict[str, FlagSpec] = {spec.name: spec for spec in FLAGS}


def get_flag(name: str) -> FlagSpec:
    """Return the registered ``FlagSpec`` for ``name`` or raise ``KeyError``."""

    try:
        return FLAGS_BY_NAME[name]
    except KeyError as exc:  # noqa: PERF203 - explicit, readable error
        raise KeyError(
            f"Unknown flag {name!r}; register it in magi_agent/config/flags.py FLAGS"
        ) from exc


def _resolve_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def flag_bool(name: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Read a registered ``bool`` flag using the strict-truthy convention.

    Unset / unrecognised values fall back to the registry default. Reading a
    non-``bool`` flag (or an unregistered name) raises rather than silently
    coercing.
    """

    spec = get_flag(name)
    if spec.kind != "bool":
        raise TypeError(f"flag {name!r} has kind {spec.kind!r}, not 'bool'")
    raw = _resolve_env(env).get(name)
    if raw is None:
        return bool(spec.default)
    return _is_true(raw)


def flag_profile_bool(name: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Read a registered ``profile_bool`` flag (profile-aware default-ON).

    Delegates to ``env._runtime_feature_enabled`` so there is exactly one source
    of truth: an explicit ``"0"/"false"/...`` always wins; an explicit
    ``"1"/"true"/...`` always wins; an unset/unrecognised value resolves to ON in
    the full runtime profile and OFF under ``MAGI_RUNTIME_PROFILE`` in
    ``safe``/``eval``/``minimal``/``conservative``/``off``.

    Reading a non-``profile_bool`` flag raises so a strict-truthy gate can never
    be misread through the profile-aware path (and vice versa via ``flag_bool``).
    """

    spec = get_flag(name)
    if spec.kind != "profile_bool":
        raise TypeError(f"flag {name!r} has kind {spec.kind!r}, not 'profile_bool'")
    return _runtime_feature_enabled(_resolve_env(env), name)


def flag_str(name: str, *, env: Mapping[str, str] | None = None) -> str | None:
    """Read a registered ``str`` flag, falling back to the registry default."""

    spec = get_flag(name)
    if spec.kind != "str":
        raise TypeError(f"flag {name!r} has kind {spec.kind!r}, not 'str'")
    raw = _resolve_env(env).get(name)
    if raw is None:
        return spec.default  # type: ignore[return-value]
    return raw


def flag_int(name: str, *, env: Mapping[str, str] | None = None) -> int | None:
    """Read a registered ``int`` flag; invalid values fall back to the default."""

    spec = get_flag(name)
    if spec.kind != "int":
        raise TypeError(f"flag {name!r} has kind {spec.kind!r}, not 'int'")
    raw = _resolve_env(env).get(name)
    if raw is None:
        return spec.default  # type: ignore[return-value]
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return spec.default  # type: ignore[return-value]
