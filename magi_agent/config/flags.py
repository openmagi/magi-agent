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
        "MAGI_CHANNEL_LIVE_DISCORD",
        stage="stage3",
        summary=(
            "Enable the live Discord channel watcher (self-host only): inbound "
            "gateway messages drive a governed turn and the reply is delivered "
            "back to the same channel. Requires MAGI_DISCORD_BOT_TOKEN and the "
            "discord extra (pip install magi-agent[discord])."
        ),
    ),
    FlagSpec(
        name="MAGI_DISCORD_BOT_TOKEN",
        default="",
        scope="public",
        stage="stage3",
        summary=(
            "Discord bot token for the live channel watcher; unset keeps the "
            "watcher fail-closed (not built). Never logged or persisted."
        ),
        kind="str",
    ),
    _b(
        "MAGI_CHANNEL_LIVE_SLACK",
        stage="stage3",
        summary=(
            "Enable the live Slack channel watcher (self-host only): inbound "
            "Socket Mode messages drive a governed turn and the reply is "
            "delivered back to the same channel/thread. Requires "
            "MAGI_SLACK_APP_TOKEN + MAGI_SLACK_BOT_TOKEN and the slack extra "
            "(pip install magi-agent[slack])."
        ),
    ),
    FlagSpec(
        name="MAGI_SLACK_APP_TOKEN",
        default="",
        scope="public",
        stage="stage3",
        summary=(
            "Slack app-level token (xapp-) authorising the Socket Mode inbound "
            "websocket; unset keeps the watcher fail-closed. Never logged."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_SLACK_BOT_TOKEN",
        default="",
        scope="public",
        stage="stage3",
        summary=(
            "Slack bot token (xoxb-) for outbound replies (chat.postMessage); "
            "unset keeps the watcher fail-closed. Never logged or persisted."
        ),
        kind="str",
    ),
    _b(
        "MAGI_OBSERVABILITY_ENABLED",
        summary="Enable the hook-tap observability module (bot-activity visibility).",
    ),
    _b(
        "MAGI_SESSION_TRANSCRIPT_ENABLED",
        summary=(
            "Write per-session JSONL debug transcripts (turn stages, tool "
            "calls, subagent spawns, messages) under the observability home."
        ),
    ),
    _b(
        "MAGI_SERVE_EVIDENCE_ENABLED",
        summary=(
            "Write per-turn tool evidence JSONL from the hosted gate5b4c3 "
            "serving runner (default-OFF; default dir = observability "
            "home/evidence, overridable via MAGI_EVIDENCE_LEDGER_DIR)."
        ),
    ),
    _b(
        "MAGI_WORK_QUEUE_ENABLED",
        summary="Enable the durable multi-agent work-queue (task board + dispatcher).",
    ),
    _b(
        "MAGI_WORK_QUEUE_EXECUTOR_ENABLED",
        summary="Enable the durable work-queue dispatcher tick loop.",
    ),
    FlagSpec(
        name="MAGI_SESSION_TRANSCRIPT_RETENTION_DAYS",
        default=14,
        scope="public",
        stage="stage1",
        summary="Session-transcript retention in days; older files are pruned.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_SESSION_TRANSCRIPT_MAX_FILES",
        default=500,
        scope="public",
        stage="stage1",
        summary="Max session-transcript files kept; oldest beyond this are pruned.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_USAGE_PRICE_IN_PER_MTOK",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Local Usage dashboard: USD per 1M INPUT tokens for the active "
            "model. Set this (and the OUT counterpart) to price models litellm "
            "does not have in its map; overrides litellm pricing when set."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_USAGE_PRICE_OUT_PER_MTOK",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Local Usage dashboard: USD per 1M OUTPUT tokens for the active "
            "model. Pairs with MAGI_USAGE_PRICE_IN_PER_MTOK."
        ),
        kind="str",
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
    _b(
        "MAGI_PERSISTENT_PYTHON_ENABLED",
        summary=(
            "Register + bind the neutral tools-persistent-python pack's "
            "PersistentPython tool (CodeAct: persistent interpreter namespace)."
        ),
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
        "MAGI_RECIPE_ROUTING_LLM_ENABLED",
        stage="stage2",
        summary=(
            "Let the model select recipe packs by their when_to_use descriptions "
            "instead of the selector-membership path; strict default-OFF (OFF is "
            "byte-identical to today)."
        ),
    ),
    _b(
        "MAGI_KERNEL_RECIPE_PACKS_ENABLED",
        stage="stage2",
        summary=(
            "Fold kernel-loaded `recipe` provides (pack.toml [[provides]] "
            "type='recipe'; bundled first-party + ~/.magi/packs + <cwd>/.magi/packs) "
            "into the recipe-compile PackRegistry. The kernel already materialises a "
            "recipe spec into a genuine RecipePackManifest; this flag makes the "
            "compiler consume them. First-party packs register first and win on a "
            "colliding pack_id (a kernel pack cannot shadow first-party); discovery "
            "failures fail closed to the first-party-only registry. Strict "
            "default-OFF (OFF is byte-identical to today)."
        ),
    ),
    _b(
        "MAGI_KERNEL_ROLE_PROVIDES_ENABLED",
        stage="stage2",
        summary=(
            "Recognise kernel `role` provides (pack.toml [[provides]] type='role'; "
            "a declarative RoleManifest scope label) in the harness preset "
            "resolution. First-party roles (general/coding/research) plus validated "
            "`ext.<name>` external roles; an external role cannot impersonate a "
            "first-party role or claim hard-safety, and the harness always keeps "
            "hard-safety in its effective packs. Contained: the engine/parallel/"
            "inference/evidence AgentRole literals are NOT widened, so an external "
            "role is a scope label only. Strict default-OFF (OFF is byte-identical "
            "to today)."
        ),
    ),
    _b(
        "MAGI_WORKER_ROUTING_LLM_ENABLED",
        stage="stage2",
        summary=(
            "Honour a planner-emitted worker_role for subagent routing instead of "
            "keyword inference; strict default-OFF (OFF is byte-identical to today)."
        ),
    ),
    _b(
        "MAGI_KEY_AWARE_MODEL_ROUTES_ENABLED",
        stage="stage2",
        summary=(
            "Filter advertised and accepted child-spawn model routes to only those "
            "whose provider has a configured API key; strict default-OFF (OFF is "
            "byte-identical to today; no-key setups always fail-open to legacy routes)."
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
    _pb(
        "MAGI_CUSTOMIZE_VERIFICATION_ENABLED",
        stage="stage2",
        summary=(
            "Master switch for the dashboard Customize tab's verification "
            "presets/rules. Profile-aware default-ON (full runtime profile; OFF "
            "under safe/eval). When on, persisted verification overrides "
            "(~/.magi/customize.json) translate into the recipe-driven pre-final "
            "evidence gate. With NO overrides this is byte-identical (opt_out "
            "seams are remove-only; opt-in seams + custom rules add nothing until "
            "the user configures them)."
        ),
    ),
    _pb(
        "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED",
        stage="stage2",
        summary=(
            "Load and compile dashboard Customize *custom rules* "
            "(~/.magi/customize.json verification.custom_rules). Profile-aware "
            "default-ON (full profile; OFF under safe/eval). Requires "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED. With no custom rules this is "
            "byte-identical (rules persist but add nothing until the user builds "
            "them)."
        ),
    ),
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
    _b(
        "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED",
        stage="stage2",
        summary=(
            "Project the live turn's inspected-source ledger into the engine's "
            "harvested public refs as the named ref "
            "'verifier:research-source-evidence' so a recipe can require source "
            "grounding: a turn that read >=1 source passes, a turn that read none "
            "blocks. Strict default-OFF and inert unless explicitly set."
        ),
    ),
    _b(
        "MAGI_GATE5B_GOVERNANCE_ENABLED",
        stage="stage2",
        summary=(
            "Run cli/engine-parity governance on the gate5b serving path: attach "
            "the control-plane plugin (loop-guard / compaction / edit-retry / "
            "self-review / max-steps / tool-synthesis etc., each behind its own "
            "existing flag) to the gate5b runner AND run a pre-final "
            "evidence/fact-grounding check over the turn's tool evidence before "
            "emitting the user-visible response. Strict default-OFF: when unset "
            "the gate5b path is byte-identical to today."
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
    # Strict default-OFF (flat _b, NOT profile-resolved): real-token accounting
    # for compaction. When ON the compaction decision uses the real prompt-token
    # count of the prior model call against a %-of-window threshold instead of the
    # char-estimate + fixed token threshold. OFF is byte-identical to today.
    _b(
        "MAGI_COMPACTION_REAL_TOKENS_ENABLED",
        stage="stage2",
        summary=(
            "Use the real prompt-token count of the prior model call against a "
            "percentage of the model's context window as the compaction budget "
            "signal, instead of the char-estimate + fixed token threshold. Strict "
            "default-OFF (OFF is byte-identical to today)."
        ),
    ),
    FlagSpec(
        name="MAGI_COMPACTION_REAL_TOKENS_PCT",
        default="0.75",
        scope="public",
        stage="stage2",
        summary=(
            "Fraction (0,1] of the model's effective context window "
            "(window - output reserve) at which real-token compaction fires; only "
            "consulted when MAGI_COMPACTION_REAL_TOKENS_ENABLED is on."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_COMPACTION_OUTPUT_RESERVE",
        default=8_000,
        scope="public",
        stage="stage2",
        summary=(
            "Tokens reserved for model output, subtracted from the context window "
            "before applying the real-token compaction percentage (>= 0); only "
            "consulted when MAGI_COMPACTION_REAL_TOKENS_ENABLED is on."
        ),
        kind="int",
    ),
    # Strict default-OFF (flat _b, NOT profile-resolved): G4 deterministic
    # tool-output prune pre-tier. When ON, OLD function_response payloads are
    # content-cleared before the Phase-1 compaction decision (cheaper, lower-loss
    # than dropping whole turns). OFF is byte-identical to today.
    _b(
        "MAGI_COMPACTION_TOOL_PRUNE_ENABLED",
        stage="stage2",
        summary=(
            "Content-clear OLD tool-output (function_response) payloads as a "
            "deterministic pre-tier before the context-compaction tail-drop "
            "decision, protecting the recent tail and protected tool results. "
            "Strict default-OFF (OFF is byte-identical to today)."
        ),
    ),
    FlagSpec(
        name="MAGI_COMPACTION_PRUNE_PROTECT",
        default=40_000,
        scope="public",
        stage="stage2",
        summary=(
            "Most-recent tool-output tokens to protect from the G4 prune pre-tier "
            "(>= 1); only consulted when MAGI_COMPACTION_TOOL_PRUNE_ENABLED is on."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_COMPACTION_PRUNE_MINIMUM",
        default=20_000,
        scope="public",
        stage="stage2",
        summary=(
            "Minimum total freed tokens required to commit a G4 tool-output prune "
            "(>= 1, no churn for tiny savings); only consulted when "
            "MAGI_COMPACTION_TOOL_PRUNE_ENABLED is on."
        ),
        kind="int",
    ),
    # Strict default-OFF (flat _b, NOT profile-resolved): G1 LLM summary injection
    # on the context-compaction tail-drop. When ON, the dropped prefix is replaced
    # by a session-model summary head (plus protected-tool-output text) instead of
    # being silently dropped. OFF is byte-identical to today (no LLM call).
    _b(
        "MAGI_COMPACTION_SUMMARIZE_ENABLED",
        stage="stage2",
        summary=(
            "Inject an LLM summary (session model) of the dropped prefix plus "
            "protected-tool-output text on a context-compaction tail-drop, instead "
            "of silently dropping. Strict default-OFF (OFF is byte-identical to "
            "today)."
        ),
    ),
    FlagSpec(
        name="MAGI_COMPACTION_SUMMARY_MODEL",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Optional model override for the G1 compaction summary; empty uses the "
            "session model (llm_request.model). Only consulted when "
            "MAGI_COMPACTION_SUMMARIZE_ENABLED is on."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_COMPACTION_SUMMARY_TIMEOUT",
        default="30",
        scope="public",
        stage="stage2",
        summary=(
            "Timeout in seconds for the G1 compaction summary model call (> 0); on "
            "timeout the tail-drop falls back to the pure prefix drop. Only "
            "consulted when MAGI_COMPACTION_SUMMARIZE_ENABLED is on."
        ),
        kind="str",
    ),
    # Strict default-OFF (flat _b, NOT profile-resolved): G5 anchored/incremental
    # summary. When ON (and MAGI_COMPACTION_SUMMARIZE_ENABLED is also ON), the prior
    # injected summary is fed back as a previous-summary anchor so the model
    # updates/merges it instead of re-summarizing from scratch. OFF is byte-
    # identical to today (plain Phase-3 summary path).
    _b(
        "MAGI_COMPACTION_ANCHORED_SUMMARY_ENABLED",
        stage="stage2",
        summary=(
            "Anchored/incremental compaction summary: feed the prior injected "
            "summary as a previous-summary anchor so the model updates/merges "
            "instead of re-summarizing from scratch (requires "
            "MAGI_COMPACTION_SUMMARIZE_ENABLED). Strict default-OFF (OFF is "
            "byte-identical to today)."
        ),
    ),
    FlagSpec(
        name="MAGI_COMPACTION_SUMMARY_MAX_FAILURES",
        default=3,
        scope="public",
        stage="stage2",
        summary=(
            "Consecutive summary-failure circuit breaker: after this many "
            "consecutive failed summary attempts in a session, skip the summarizer "
            "and fall back to pure tail-drop; reset on success. 0 disables the "
            "breaker. Only consulted when MAGI_COMPACTION_SUMMARIZE_ENABLED is on."
        ),
        kind="int",
    ),
    # Strict default-OFF (flat _b, NOT profile-resolved): G7 manual /compact
    # force-compaction. When ON, the /compact command sets a cross-turn one-shot
    # signal and the compaction plugin forces a tail-drop on the next model turn
    # regardless of token threshold (reusing the existing G1/G4/G5/G8 machinery).
    # Only has effect when MAGI_CONTEXT_COMPACTION_ENABLED is ALSO on (the plugin
    # is only attached in the control_plane build when compaction is enabled). OFF
    # keeps the /compact stub acknowledgement byte-identical to today.
    _b(
        "MAGI_COMPACTION_MANUAL_ENABLED",
        stage="stage2",
        summary=(
            "Make manual /compact actually force a context compaction on the next "
            "model turn (cross-turn one-shot signal consumed by the compaction "
            "plugin), regardless of token threshold. Requires "
            "MAGI_CONTEXT_COMPACTION_ENABLED. Strict default-OFF (OFF keeps the "
            "/compact stub acknowledgement byte-identical to today)."
        ),
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
    # --- Subagent / child-runner --------------------------------------------
    _b(
        "MAGI_SUBAGENT_GOVERNED_TURN_ENABLED",
        summary=(
            "Route spawned subagents through run_governed_turn (governed "
            "turn-loop) instead of the bare run_async child loop. Default OFF "
            "keeps the legacy child path byte-identical."
        ),
    ),
    _b(
        "MAGI_CHILD_MEMORY_INHERIT_ENABLED",
        summary=(
            "When ON, a normal-mode parent yields a read_only child memory mode "
            "(child reads parent workspace memory, never writes back). Default "
            "OFF keeps children incognito."
        ),
    ),
    _b(
        "MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED",
        summary=(
            "Intersect a spawned subagent's tool set with the parent's effective "
            "tools (tighten-only) at child-runtime build. Default OFF / empty "
            "parent cap is a no-op."
        ),
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
            "Runtime profile selector (safe/off/minimal/conservative/eval/lab). "
            "Safe profiles disable default-ON resilience seams; lab opts into the "
            "full experimental flat-flag tier (local-full + experimental extras)."
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
