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

# Reuse the single truthy convention. The shared primitives live in the
# dependency-free leaf ``config/_truthy.py`` (I-3); ``env.py`` re-exports the
# same callables under their historic private names. Importing the leaf
# directly here keeps ``flags.py`` independent of ``env.py`` so the managed
# import cycle stays broken — see tests/test_config_import_acyclic.py.
from ._truthy import (
    is_true as _is_true,
    runtime_feature_enabled as _runtime_feature_enabled,
)

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
        "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED",
        summary=(
            "Enable PR4 R2 corrective recovery: when the main agent ends "
            "a turn with zero text after tool calls, the engine re-invokes "
            "once with a 'produce your final answer now' nudge. Default-OFF "
            "in the registry; LAB_EXPERIMENTAL_FLAGS opts it in for lab / "
            "dogfood profiles so the dashboard stops showing the "
            "'no final answer text arrived' fallback banner."
        ),
    ),
    _b(
        "MAGI_GOAL_NUDGE_ENABLED",
        summary=(
            "Enable the production goal-nudge: a bounded continuation that "
            "fires when MagiEngineDriver detects a clean stop short of the "
            "stated goal (strict default-OFF; OFF injects goal_nudge=None and "
            "the driver behaves byte-identically to pre-PR4)."
        ),
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
        "MAGI_RICH_TOOL_PREVIEW",
        summary=(
            "Expose human-readable tool-call argument summaries in the public "
            "activity timeline (e.g. SpawnAgent task/persona, Bash command, file "
            "path/content head). Default-OFF: when off, private-key args stay "
            "digested (byte-identical to today). Allowlisted keys still pass the "
            "full secret/PII sanitizer; system/user/raw prompts stay redacted."
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
    _b(
        "MAGI_WORK_QUEUE_BOARD_API_ENABLED",
        summary="Mount the read-only work-queue board HTTP API.",
    ),
    _b(
        "MAGI_WORK_QUEUE_NOTIFY_ENABLED",
        summary=(
            "Enable the work-queue terminal-event notifier gateway watcher: polls "
            "for newly-completed/blocked/failed tasks and pushes each through the "
            "injected delivery sink (default sink = logging-only; real channel sinks "
            "are wired in P6). Default-OFF; when off the daemon does not poll."
        ),
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
        "MAGI_GROUNDED_ANSWER_GUARD_ENABLED",
        summary=(
            "Enable the grounded-answer guard: caller (GAIA harness / CLI "
            "layer) may compute a GroundedAnswerVerdict against its collected "
            "tool corpus and record verifierEvidenceStatus as out-of-band "
            "metadata. Strict default-OFF; OFF keeps callers' prompt and "
            "answer surfaces byte-identical."
        ),
    ),
    _b(
        "MAGI_PERSIST_RUN_BOOKENDS_ENABLED",
        summary=(
            "Persist a per-turn run-bookend record (goal, one-line result, "
            "model, token usage, status) to the durable evidence ledger so a "
            "run-share page can render the top summary. Strict default-OFF; OFF "
            "keeps the evidence ledger byte-identical (no extra record written)."
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
    _pb(
        "MAGI_READ_LEDGER_ENABLED",
        summary=(
            "Record full reads in the per-turn ledger and enforce read-before-edit "
            "on the gate5b full toolhost (default-ON full profile)."
        ),
    ),
    _pb(
        "MAGI_READ_QUALITY_ENABLED",
        summary=(
            "Quality-of-life FileRead output: 1-indexed line numbers, line/byte "
            "caps with continue-offset footer, binary detection, did-you-mean "
            "filename suggestions on miss (default-ON full profile)."
        ),
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
        "MAGI_KERNEL_RECIPE_ENTRY_POINTS_ENABLED",
        stage="stage2",
        summary=(
            "Discover external recipe packs via the Python `magi.recipes` "
            "entry_points group on top of the filesystem source. AND'd with "
            "MAGI_KERNEL_RECIPE_PACKS_ENABLED. EntryPoint.load() imports the "
            "publisher's module (the standard distribution-tool trust model — "
            "like pytest plugins), so this is self-host opt-in only; the hosted "
            "floor must never enable it. Only inert DATA manifests are accepted; "
            "callable / code-carrying payloads are dropped. External publishers "
            "go through the same compose-only validation as user-dir packs "
            "(R1/R4/R6/R7). Strict default-OFF (OFF is byte-identical)."
        ),
    ),
    _b(
        "MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED",
        stage="stage2",
        summary=(
            "Discover external verifier manifests via the Python `magi.verifiers` "
            "entry_points group and merge them into the default verifier bus via "
            "the tighten-only `with_additional_verifiers` helper. EntryPoint.load() "
            "imports the publisher's module (the standard distribution-tool trust "
            "model — like pytest plugins), so this is self-host opt-in only; the "
            "hosted floor must never enable it. Only inert DATA manifests are "
            "accepted; callable / code-carrying payloads are dropped. Tighten-only: "
            "an external cannot overwrite an existing verifier id, invade the "
            "hard-safety priority band, or claim hard-safety / security-critical "
            "authority. Strict default-OFF (OFF is byte-identical)."
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
    # --- Runner-policy routing (cli/engine.py authority-adjacent seams) ----
    # Authority-adjacent: route selection / blocking / recipe-intent binding
    # are all gated default-OFF. Promoted from cli/engine.py module-local
    # denylist parsers to the registry as part of I-2 PR A (truthy
    # consolidation) so the strict allowlist semantics live in exactly one
    # place. Default-OFF stays default-OFF; an unset env reads as OFF.
    _b(
        "MAGI_RUNNER_POLICY_ROUTING_ENABLED",
        stage="stage2",
        summary=(
            "Emit and attach safe runner-policy routes during phase routing "
            "(cli/engine._runner_policy_routing_enabled). Code default OFF; "
            "installed/local full-runtime profiles and hosted canary profiles "
            "opt in explicitly via env."
        ),
    ),
    _b(
        "MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED",
        stage="stage2",
        summary=(
            "Hard-block denied materialised routes before provider calls "
            "(cli/engine._runner_policy_route_blocking_enabled). Intentionally "
            "NOT part of the default full-runtime profile: materialised denials "
            "can be stale while the configured model is still capable. Default "
            "OFF (route denials emit audit metadata and the turn continues)."
        ),
    ),
    _b(
        "MAGI_RECIPE_INTENT_BINDING_ENABLED",
        stage="stage2",
        summary=(
            "Bind emit-only recipe intents (provider / channel / artifact / "
            "scheduler) to hint-level runner effects (doc 05 PR-3 / A1-G2). "
            "Strict default-OFF (OFF byte-identical to today). Hard enforcement "
            "stays deferred to 14-controlplane."
        ),
    ),
    # NOTE: flat default-ON; cli/wiring._first_party_tools_enabled treats unset
    # as True. Promoted to the registry as part of I-2 PR A so the truthy set
    # is read in exactly one place.
    _b(
        "MAGI_FIRST_PARTY_TOOLS_ENABLED",
        default=True,
        summary=(
            "Mount the first-party Magi tool pack on the CLI runner; flat "
            "default-ON. Set ``=0`` to fall back to ADK-native tools only."
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
        "MAGI_MODEL_REASONING_DEFAULT_ON",
        stage="stage2",
        summary=(
            "Source per-model default reasoning kwargs (Opus adaptive thinking, "
            "Sonnet/GPT-5.5/Gemini 3.1 Pro reasoning_effort=high) from the "
            "ModelCatalog so a fresh install benchmarks the flagship models the "
            "way published numbers were measured. Default-OFF for soak (OFF is "
            "byte-identical to today); env knobs MAGI_MODEL_THINKING_TYPE / "
            "MAGI_MODEL_THINKING_BUDGET_TOKENS / MAGI_MODEL_REASONING_EFFORT "
            "remain overrides on top, and MAGI_MODEL_REASONING_EFFORT=off/none "
            "is an explicit disable that wins even when the flag is on."
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
    _b(
        "MAGI_AUTOMATION_METHODOLOGY_ENABLED",
        summary=(
            "Append the <automation_methodology> prompt block (deliverable up "
            "front / goal->plan->evidence lifecycle / step confirmation) in "
            "build_cli_instruction. Guidance-only, not enforcing."
        ),
    ),
    _b(
        "MAGI_CODING_CONTEXT_ENABLED",
        summary=(
            "Append the C10 <coding_context> auto-injection prompt block "
            "(repo map + recent git changes + entry points + top-level "
            "directory stats) in build_cli_instruction when workspace_root is "
            "provided. Guidance-only, not enforcing."
        ),
    ),
    _b(
        "MAGI_PROMPT_EXAMPLES_ENABLED",
        summary=(
            "Append the <action_discipline_examples> prompt block "
            "(positive/negative contrast pairs: act-vs-ask, finish-vs-defer) "
            "in build_cli_instruction."
        ),
    ),
    _b(
        "MAGI_PROMPT_REDFLAGS_ENABLED",
        summary=(
            "Append the <red_flags> anti-rationalization prompt block (\"this "
            "thought means stop and correct course\" table) in "
            "build_cli_instruction."
        ),
    ),
    _b(
        "MAGI_PROMPT_SEARCH_RULES_ENABLED",
        summary=(
            "Append the search-decision heuristics prompt block in "
            "build_cli_instruction. Even when ON the block only fires when web "
            "tools are available (BRAVE_API_KEY AND FIRECRAWL_API_KEY)."
        ),
    ),
    _b(
        "MAGI_RESEARCH_METHODOLOGY_ENABLED",
        summary=(
            "Append the <research_methodology> prompt block (multi-source "
            "cross-check / grounding-first / primary-source preference / "
            "citation discipline) in build_cli_instruction. Guidance-only."
        ),
    ),
    _b(
        "MAGI_TOOL_USAGE_GUIDANCE_ENABLED",
        summary=(
            "Synthesize per-tool 'Use when / Do NOT use when' usage-guidance "
            "blocks into gate5b ADK tool descriptions. OFF keeps every gate5b "
            "tool docstring byte-identical to today."
        ),
    ),
    _b(
        "MAGI_HOSTED_FULL_ACCESS",
        summary=(
            "Grant the local headless engine path 'bypassPermissions' for a "
            "trusted hosted bot (single-tenant / self-host) so mutating and "
            "execution tools (Bash, SpawnAgent, FileWrite) run without an "
            "interactive approver. OFF keeps the normal 'default' gate where "
            "mutating tools are safe-denied headless. Only effective when the "
            "request reaches the local engine path (hosted-streaming-serve OFF "
            "and no gate5b user-visible canary gate); the gateway token remains "
            "the sole access boundary."
        ),
    ),
    _b(
        "MAGI_USER_HOOKS_ENABLED",
        summary=(
            "Master gate for CC-style user settings.json hooks (self-host / "
            "local CLI only — never hosted multi-tenant since command hooks "
            "run operator-supplied `bash -c`). When ON the CLI engine loads "
            "~/.magi/settings.json + <workspace>/.magi/settings.json, builds a "
            "HookBus wired to the command executor, and bridges "
            "PreToolUse/PostToolUse onto the ADK before/after-tool callbacks. "
            "Strict default-OFF; OFF keeps every turn byte-identical."
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
        "MAGI_VERIFY_ANSWER_QUALITY",
        stage="stage2",
        summary=(
            "Block a final answer that does not genuinely address the user's "
            "task (empty / pure tool-or-JSON echo / clearly unrelated) via the "
            "LLM criterion judge; requires a critic model "
            "(MAGI_EGRESS_GATE_ENABLED). Strict default-OFF and inert unless "
            "explicitly set (or the answer-quality Customize preset is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_PRE_REFUSAL",
        stage="stage2",
        summary=(
            "Block a final answer that refuses a doable task without any attempt "
            "or a legitimate reason (safety / genuinely impossible / missing "
            "info) via the LLM criterion judge; requires a critic model "
            "(MAGI_EGRESS_GATE_ENABLED). Strict default-OFF and inert unless "
            "explicitly set (or the pre-refusal Customize preset is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_COMPLETION_EVIDENCE",
        stage="stage2",
        summary=(
            "Block a final answer that claims completion or promises future "
            "delivery while the turn produced no action/tool evidence, via the "
            "LLM criterion judge (merged completion-evidence / goal-progress / "
            "deferral-blocker concern); requires a critic model "
            "(MAGI_EGRESS_GATE_ENABLED). Strict default-OFF and inert unless "
            "explicitly set (or one of those Customize presets is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_RESOURCE_CLAIM",
        stage="stage2",
        summary=(
            "Block a final answer that asserts a specific resource exists / was "
            "read / was checked (file path, URL, 'I read X', memory) while the "
            "turn produced no source/read evidence, via the LLM criterion judge "
            "(merged self-claim / resource-existence concern); requires a critic "
            "model (MAGI_EGRESS_GATE_ENABLED). Strict default-OFF and inert "
            "unless explicitly set (or one of those Customize presets is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_CLAIM_CITATION",
        stage="stage2",
        summary=(
            "Block a final answer that makes specific factual claims without any "
            "source citation, via the LLM criterion judge (free-text "
            "claim-coverage; distinct from source-authority anti-fab over "
            "declared src_N refs); requires a critic model "
            "(MAGI_EGRESS_GATE_ENABLED). Strict default-OFF and inert unless "
            "explicitly set (or the claim-citation Customize preset is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_OUTPUT_PURITY",
        stage="stage2",
        summary=(
            "Block a final answer that leaks internal data (raw tool-result "
            "envelopes, internal reasoning traces, canonical private payload "
            "keys in JSON shape) via the LLM criterion judge; requires a critic "
            "model (MAGI_EGRESS_GATE_ENABLED). Strict default-OFF and inert "
            "unless explicitly set (or the output-purity Customize preset is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_TASKBOARD_COMPLETION",
        stage="stage2",
        summary=(
            "Block turn completion while the workspace .magi/taskboard.jsonl "
            "still has a task in a non-terminal status; strict default-OFF and "
            "inert unless explicitly set (or the task-board-completion Customize "
            "preset is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_PARALLEL_RESEARCH",
        stage="stage2",
        summary=(
            "Block a research-recipe turn that synthesized from fewer than the "
            "minimum number of inspected sources (cross-check requirement); "
            "strict default-OFF and inert unless explicitly set (or the "
            "parallel-research Customize preset is enabled)."
        ),
    ),
    _b(
        "MAGI_VERIFY_RESPONSE_LANGUAGE",
        stage="stage2",
        summary=(
            "Block a final answer that violates the configured language policy "
            "(MAGI_RESPONSE_LANGUAGE, e.g. 'ko') by wiring the dormant "
            "discipline_boundary.response_language check to the pre-final gate; "
            "strict default-OFF and inert unless explicitly set (or the "
            "response-language Customize preset is enabled) AND a policy is set."
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
    # Tri-state mode flag (off/advisory/block), NOT a strict-truthy bool.
    # ``resolve_document_authoring_coverage_mode`` parses the mode; the legacy
    # truthy form (``1``/``true``/``yes``/``on``) is recognised at resolver level
    # as ``block`` for back-compat. Registered as ``kind="str"`` so the typed
    # reader returns the raw string and the resolver maps to the 3-mode space.
    FlagSpec(
        name="MAGI_DOCUMENT_AUTHORING_COVERAGE",
        default="off",
        scope="public",
        stage="stage1",
        summary=(
            "Tri-state document-coverage gate (off|advisory|block). off keeps the "
            "DocumentCoverage evidence audit-only; advisory records counts without "
            "blocking; block flips the pre-final verifier-bus decision when "
            "coverage fails. Legacy truthy values resolve to ``block`` for "
            "back-compat. Strict default ``off``."
        ),
        kind="str",
    ),
    _b(
        "MAGI_SHACL_VERIFIER_ENABLED",
        summary=(
            "Activate the SHACL constraint consume-side gate in the pre-final "
            "verifier bus. When ON, any ShaclConstraintCheck evidence record with "
            "top-level status='failed' flips the pre-final decision to 'block'. "
            "Strict default-OFF; OFF is byte-identical to before (fail-safe 'unknown' "
            "records and 'ok' records never block). "
            "Enabling this flag without an active SHACL producer wired (PR1 state) "
            "has no effect: no ShaclConstraintCheck records are emitted until PR2."
        ),
        scope="public",
        stage="stage2",
    ),
    _b(
        "MAGI_SHACL_COMPILER_ENABLED",
        summary=(
            "Enable the POST /v1/app/customize/custom-rules/compile endpoint: "
            "NL→SHACL Turtle compilation via LLM (registration-time only, never "
            "on the runtime hot path). OFF keeps the compile route dormant and "
            "all other customize routes byte-identical. Requires a configured "
            "provider/key for LLM calls; fail-open (returns ok=False) when no "
            "model is available. Strict default-OFF."
        ),
        scope="public",
        stage="stage2",
    ),
    _b(
        "MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED",
        summary=(
            "Enable the customize PresetSeam NL-spec endpoints: POST "
            "/v1/app/customize/seams/compile (NL→SeamSpec via LLM, "
            "registration-time only), PUT /v1/app/customize/seams (persist an "
            "approved spec), DELETE /v1/app/customize/seams/{id}. OFF keeps "
            "the routes dormant and the runtime ``seam_for_user`` returns the "
            "byte-identical builtin seam (no spec is read from the overrides "
            "file). Strict default-OFF; the runtime hot path is unchanged."
        ),
        scope="public",
        stage="stage2",
    ),
    _b(
        "MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED",
        summary=(
            "Enable the POST /v1/app/customize/rules/compile endpoint: a "
            "single NL → rule compiler that auto-routes the user's policy to "
            "one of six backing primitives (deterministic_ref / tool_perm / "
            "llm_criterion / shacl_constraint / seam_spec / custom_check). "
            "Returns a structured draft + LLM critic verdict + deterministic "
            "schemaIssues; the caller activates by hitting the matching "
            "existing PUT route (custom-rules / seams / dashboard-checks). "
            "Registration-time only — never on the runtime hot path. Requires "
            "a configured provider/key; fail-open (returns ok=False) when no "
            "model is available. Strict default-OFF."
        ),
        scope="public",
        stage="stage2",
    ),
    _b(
        "MAGI_DASHBOARD_PACK_AUTHORING_ENABLED",
        summary=(
            "Self-host-only dashboard pack-builder UI/REST plus the after-tool "
            "dashboard producer control and the pre-final dashboard "
            "deny-on-present gate. When a dashboard 'block' check matches an "
            "after-tool result the producer emits a custom:DashboardCheck "
            "evidence record with top-level status='failed' and the verifier-bus "
            "gate flips the pre-final decision to 'block' (audit checks emit "
            "status='ok' and never block). This flag also arms the engine's "
            "invocation-id reconciliation fold, so the pre-final block is "
            "self-contained and does NOT require "
            "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED. With no dashboard checks "
            "authored the runtime is byte-identical to before. Strict default-OFF."
        ),
        scope="public",
        stage="stage2",
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
    _pb(
        "MAGI_MESSAGE_CACHE_ENABLED",
        summary=(
            "Mark the last ~2 non-system conversation messages with an Anthropic "
            "ephemeral ``cache_control`` marker so the growing conversation tail "
            "is prompt-cached in addition to the system prefix (default-ON full "
            "profile)."
        ),
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
    _b(
        "MAGI_SPAWN_RECIPE_CAP_ENABLED",
        summary=(
            "Apply the orchestrator's spawn_cap as the innermost tool-name ceiling "
            "in _resolve_turn_toolset, after profile and parent-cap filtering. "
            "Default OFF / None spawn_cap is a no-op (byte-identical)."
        ),
    ),
    _b(
        "MAGI_SPAWN_RECIPE_BIND_ENABLED",
        summary=(
            "Thread recipeRefs from request.metadata into the child runner as "
            "pinned_recipe_pack_ids so the child's ProfileResolver binds the "
            "parent-supplied recipe packs. Default OFF / empty refs is a no-op "
            "(byte-identical to passing pinned_recipe_pack_ids=())."
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
        "CORE_AGENT_PYTHON_CHAT_ROUTE",
        scope="hosted",
        summary=(
            "Hosted-runtime authority gate for the python chat route. ON routes "
            "the hosted ``/v1/chat/*`` and gate1a selected-attempt preflight "
            "endpoints through the Python serve path; OFF returns "
            "``chat_route_disabled`` (legacy node fallback). Strict default-OFF; "
            "hosted-only (excluded from the public env-reference)."
        ),
    ),
    # I-4 follow-up: workspace-root + local-chat-route flags promoted to the
    # registry so the ~5 inline ``os.environ.get(...)`` reads in
    # ``transport/chat_routes.py`` flow through ``flag_bool`` / ``flag_str``.
    _b(
        "MAGI_AGENT_LOCAL_CHAT_ROUTE",
        summary=(
            "Self-host fallback gate for the local ADK chat route. ON makes "
            "``/v1/chat/completions`` serve the local headless engine when the "
            "hosted python chat route is OFF; OFF keeps the legacy "
            "``chat_route_disabled`` 503. Strict default-OFF."
        ),
    ),
    FlagSpec(
        name="MAGI_AGENT_WORKSPACE",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Workspace directory used by the local chat route, headless CLI "
            "wiring, and per-turn memory recall; empty falls back to "
            "``os.getcwd()`` (the historical default)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b full-toolhost workspace root; empty falls back to "
            "``Path.cwd()`` (the historical default). Hosted-only (excluded "
            "from the public env-reference)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_WORKSPACE_ROOT",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate1a read-only toolhost workspace root; empty falls back "
            "to ``Path.cwd()`` (the historical default). Hosted-only (excluded "
            "from the public env-reference)."
        ),
        kind="str",
    ),
    # I-4 follow-up: control_plane MaxStepsBrake gate promoted so the inline
    # ``_is_true(env.get(...))`` in ``adk_bridge/control_plane.py`` flows
    # through ``flag_bool``. Coordination note (commit body): H-9 flags
    # ``MaxStepsBrakeControl`` as an inert no-op (max_iterations=0); H-9 may
    # delete this gate entirely. Keep the registration thin so the deletion is
    # a one-line follow-up.
    _b(
        "MAGI_MAX_STEPS_BRAKE_ENABLED",
        summary=(
            "Register the MaxStepsBrakeControl wrap-up brake on the control "
            "plane (the seam is wired with ``max_iterations=0``; H-9 audit may "
            "delete the seam entirely). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_HOSTED_GOVERNED_TURN_ENABLED",
        scope="hosted",
        summary=(
            "Route hosted serving turns through run_governed_turn → "
            "MagiEngineDriver instead of gate5b4c3._invoke_async_turn (Phase 2 "
            "flip; default-OFF; CLI/local path unchanged). When OFF the hosted "
            "path is byte-identical to today."
        ),
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
    # --- Model knobs (E-15) -----------------------------------------------
    # The per-turn LiteLlm build (``cli/real_runner._build_litellm_model``)
    # consults eight ``MAGI_MODEL_*`` / ``MAGI_LLM_*`` knobs. They used to be
    # read directly off ``os.environ`` inline (undiscoverable). Registering
    # them here surfaces them in flag discovery and routes their reads
    # through the typed registry (``flag_int``/``flag_str``).
    FlagSpec(
        name="MAGI_MODEL_NUM_RETRIES",
        default=4,
        scope="public",
        stage="stage1",
        summary=(
            "litellm ``num_retries`` for the per-turn LiteLlm build; transient "
            "provider failures (5xx, connection drops, overloaded) are "
            "retried this many times before the run aborts. Values < 1 fall "
            "back to the default."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_MODEL_TIMEOUT_S",
        default=600,
        scope="public",
        stage="stage1",
        summary=(
            "litellm ``timeout`` (seconds) bounding a single hung request to "
            "the model provider. Values < 1 fall back to the default."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_MODEL_THINKING_TYPE",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Highest-precedence reasoning escape hatch. Set to ``adaptive`` to "
            "send ``thinking={'type': 'adaptive'}`` directly (Anthropic Opus "
            "4.7/4.8 adaptive-only path). Empty = unused."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_MODEL_THINKING_BUDGET_TOKENS",
        default=0,
        scope="public",
        stage="stage1",
        summary=(
            "Explicit Anthropic-style ``thinking={'type': 'enabled', "
            "'budget_tokens': N}`` for budget-thinking models (Sonnet 4.5/4.6). "
            "Adaptive-only models REJECT this shape; use "
            "``MAGI_MODEL_THINKING_TYPE=adaptive`` instead. <= 0 = unused."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_MODEL_REASONING_EFFORT",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Cross-provider ``reasoning_effort`` (``minimal``/``low``/"
            "``medium``/``high``/``xhigh``/``max``); ``off``/``none`` disable. "
            "Recommended knob — litellm maps it per-provider (Opus ⇒ "
            "adaptive, GPT/Gemini ⇒ effort). Per-provider normalization is "
            "applied (notably ``max`` → ``xhigh`` for OpenAI/OpenRouter)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_LLM_API_BASE",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Optional LiteLlm ``api_base`` URL routing every model build "
            "through a gateway (in-cluster api-proxy holding provider keys). "
            "Unset ⇒ direct-to-provider (default)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_LLM_API_KEY",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Credential paired with ``MAGI_LLM_API_BASE``: surfaced as the "
            "litellm ``api_key`` AND an explicit auth header so OpenAI-prefixed "
            "models still present the gateway token. SENSITIVE — never logged "
            "or persisted; treat as a secret like ``MAGI_DISCORD_BOT_TOKEN``."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_LLM_API_HEADER",
        default="x-api-key",
        scope="public",
        stage="stage1",
        summary=(
            "Auth header name used to forward ``MAGI_LLM_API_KEY`` to the "
            "gateway (default ``x-api-key``). Empty resolves to the default."
        ),
        kind="str",
    ),
    # --- Shadow serve token-estimate (E-14) ---------------------------------
    _b(
        "MAGI_SERVE_TOKEN_ESTIMATE_REAL",
        stage="stage2",
        summary=(
            "Replace ``shadow/gate5b4c3_runner_input_adapter._estimate_tokens`` "
            "from UTF-8 byte length to a real character/BPE estimate "
            "(``shared/token_estimation.count_text_tokens``). Pre-E-14 the "
            "byte heuristic over-counted ASCII ~4× and CJK ~3×, so the serve "
            "path spuriously dropped Korean/CJK turns as "
            "``input_token_budget_exceeded``. Default-OFF for soak per "
            "AGENTS.md flag-promotion-verification — flip to default-ON "
            "after canary verification. The byte cap "
            "(``max_sanitized_input_bytes``) is the real DoS guard, enforced "
            "at the contract validator and untouched by this flag."
        ),
    ),
    # --- Context management hook (I-4 batch) --------------------------------
    # ``context/hook.py`` resolves the per-turn ``ContextManagementConfig``
    # from 5 inline env reads. Registered here so the knobs are
    # discoverable in flag-discovery + the env-reference.
    _b(
        "MAGI_CONTEXT_MGMT_ENABLED",
        summary=(
            "Master switch for the per-turn context management hook "
            "(threshold-driven proactive compaction). Default-OFF; the "
            "hook is a no-op until enabled."
        ),
    ),
    _b(
        "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED",
        summary=(
            "Enable proactive context recovery (compaction triggered "
            "before the model errors out on context). Honored only when "
            "``MAGI_CONTEXT_MGMT_ENABLED`` is ON."
        ),
    ),
    FlagSpec(
        name="MAGI_CONTEXT_MODERATE_THRESHOLD",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Float utilization threshold (0.0-1.0) for the ``moderate`` "
            "context level (default 0.60). Parsed via ``_safe_float`` in "
            "``context/hook.py``; empty / unparseable falls back to default."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_CONTEXT_HIGH_THRESHOLD",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Float utilization threshold (0.0-1.0) for the ``high`` "
            "context level (default 0.75). Parsed via ``_safe_float`` in "
            "``context/hook.py``; empty / unparseable falls back to default."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_CONTEXT_CRITICAL_THRESHOLD",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Float utilization threshold (0.0-1.0) for the ``critical`` "
            "context level (default 0.90). Parsed via ``_safe_float`` in "
            "``context/hook.py``; empty / unparseable falls back to default."
        ),
        kind="str",
    ),

    # --- Document agentic authoring (I-4 batch 3) ---------------------------
    FlagSpec(
        name="MAGI_DOCUMENT_AGENTIC_MODEL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Model id for the agentic document writer "
            "(``tools/document_write/agentic.LiteLLMAgenticDocumentWriter``). "
            "Empty disables the agentic path; the document write tool falls "
            "back to its deterministic builder."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_DOCUMENT_AGENTIC_TEMPERATURE",
        default="0.2",
        scope="public",
        stage="stage1",
        summary=(
            "litellm ``temperature`` for the agentic document writer "
            "(parsed as float; default 0.2)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_DOCUMENT_AGENTIC_TIMEOUT_S",
        default=90,
        scope="public",
        stage="stage1",
        summary=(
            "litellm ``timeout`` (seconds) for the agentic document writer "
            "(default 90)."
        ),
        kind="int",
    ),
    # --- Work-queue store paths (I-4 batch 3) -------------------------------
    FlagSpec(
        name="MAGI_WORK_QUEUE_DB_PATH",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Explicit path to the work-queue SQLite db. Empty uses "
            "``<MAGI_STATE_DIR>/work_queue.db`` (default "
            "``~/.magi/work_queue.db``)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_STATE_DIR",
        default="~/.magi",
        scope="public",
        stage="stage2",
        summary=(
            "Root directory for per-user runtime state (work-queue db, "
            "session caches, etc). ``~`` is expanded."
        ),
        kind="str",
    ),
    # --- Audio / Video tool gates (I-4 batch 4) -----------------------------
    FlagSpec(
        name="MAGI_ASR_PROVIDER",
        default="openai_whisper",
        scope="public",
        stage="stage1",
        summary=(
            "Provider id for the audio transcription tool "
            "(``tools/audio_tools``). Default ``openai_whisper`` keeps the "
            "OpenAI Whisper provider; unknown values disable the path."
        ),
        kind="str",
    ),
    _b(
        "MAGI_VIDEO_DOWNLOAD_ENABLED",
        summary=(
            "Enable the video download tool path (consumed by "
            "``tools/video_tools`` and the audio-side fallback). "
            "Default-OFF — the tool returns ``disabled`` without it."
        ),
    ),
    # --- Runtime stream-withholding + fork-cache + recovery (I-4 batch 5) ---
    _b(
        "MAGI_STREAM_WITHHOLDING_ENABLED",
        summary=(
            "Enable the runtime stream-withholding buffer. When ON the "
            "streaming chat path holds tokens in a per-turn buffer so a "
            "tool-call retry can suppress + replay them; OFF emits "
            "directly."
        ),
    ),
    FlagSpec(
        name="MAGI_STREAM_WITHHOLDING_MAX_RETRIES",
        default=2,
        scope="public",
        stage="stage1",
        summary=(
            "Maximum suppress-and-retry attempts for the withholding "
            "buffer (default 2)."
        ),
        kind="int",
    ),
    _b(
        "MAGI_FORK_CACHE_ENABLED",
        summary=(
            "Enable the per-child fork-runner output cache (skips re-"
            "computation when an identical child invocation already "
            "produced a result this run). Default-OFF."
        ),
    ),
    FlagSpec(
        name="MAGI_MAX_RECOVERY_ATTEMPTS",
        default=3,
        scope="public",
        stage="stage1",
        summary=(
            "Maximum retry attempts the error-recovery framework will "
            "make for a recoverable category (default 3)."
        ),
        kind="int",
    ),
    # --- TUI surface knobs (I-4 batch) --------------------------------------
    _b(
        "MAGI_TUI_FILE_MENTIONS",
        summary=(
            "Enable the TUI ``@``-mention file picker (the input bar "
            "suggests workspace paths). Default-OFF."
        ),
    ),
    _b(
        "MAGI_TUI_LEGACY_RICHLOG",
        summary=(
            "Force the TUI to render the transcript via the legacy "
            "RichLog widget instead of the streaming buffer. "
            "Default-OFF; used for diagnosing render issues."
        ),
    ),
    _b(
        "MAGI_TUI_DIFF_SPLIT",
        summary=(
            "Render Edit-tool diffs side-by-side in the TUI activity "
            "timeline. Default-OFF (unified diff)."
        ),
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
