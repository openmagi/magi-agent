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
        "MAGI_MEMORY_LOCAL_DEV",
        summary=(
            "Local single-user dev short-circuit for the memory-write readiness "
            "gate. Default-OFF; opt-in only with the readiness + write env "
            "gates ON and the kill-switch INACTIVE."
        ),
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
        "MAGI_MEMORY_REVIEW_ENABLED",
        summary=(
            "Default-OFF master switch for the background memory-review harness. "
            "When OFF the reviewer short-circuits without any write."
        ),
    ),
    _b(
        "MAGI_MEMORY_MODE_ROUTING_ENABLED",
        summary="Honour the per-channel memory mode header (normal/read-only/incognito).",
    ),
    _b(
        "MAGI_MEMORY_PROJECTION_ENABLED",
        summary="Project a lean memory view into the serve prompt block.",
    ),
    _b(
        "MAGI_MEMORY_RECALL_RERANK_ENABLED",
        summary=(
            "Default-OFF: ask the cheap rerank LLM to reorder memory-recall "
            "hits before injection. Off keeps the BM25 / vector order verbatim."
        ),
    ),
    _b(
        "MAGI_MEMORY_SESSION_EXTRACT_ENABLED",
        summary=(
            "Default-OFF master switch for the session-end fact extractor. "
            "When ON the harness summarises a closed session and (gated) "
            "persists declarative facts; OFF skips both extraction and write."
        ),
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
    _b(
        "MAGI_CUSTOMIZE_SELF_IMPROVEMENT_ENABLED",
        summary=(
            "Operator opt-in sibling gate for the Customize dashboard's Self "
            "Improvement recipe (F-LIFE5). Default-OFF. When set truthy the "
            "self-improvement recipe pack ('openmagi.self-improvement') is "
            "treated as active for evidence-ref subtraction. The two safety "
            "policies (eval-observation-required, no-direct-mutation) remain "
            "in force regardless of this flag."
        ),
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
        "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED",
        stage="stage2",
        summary=(
            "WS5 PR5b escalation: when the master empty-response recovery flag "
            "is ON and every attempt stays empty, do one bounded second "
            "recovery whose final message asks the model to produce an answer "
            "OR state what is blocking it; if still empty, stream a "
            "deterministic blocked notice (an explicit non-answer) so the turn "
            "ends honestly instead of completing blank. Default-OFF in the "
            "registry; inert unless MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED is "
            "also ON. The full self-host profile and lab opt in."
        ),
    ),
    _b(
        "MAGI_CHILD_RUNNER_EMPTY_DEBUG",
        summary=(
            "Default-OFF operator-opt-in diagnostic. When truthy, the child "
            "runner's legacy + governed collectors log one warning per turn "
            "naming the collected text_chunks count / summary length and the "
            "evidence_refs count + first ref. Lets the operator see whether "
            "the empty-response guard about to be checked will fire AND, if "
            "not, exactly why (zero text vs non-empty whitespace vs "
            "unexpected ref leakage) without redeploying a debug wheel."
        ),
    ),
    FlagSpec(
        name="MAGI_TRACE_LOG_PATH",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Override path for the file-backed diagnostic trace sink "
            "(see magi_agent/runtime/trace_sink.py). Default path "
            "~/.openmagi/trace.log is used when unset. Honours ~ and "
            "$VAR expansion. The sink exists so MAGI_CHILD_RUNNER_EMPTY_DEBUG "
            "stamps do not vanish into a wedged uvicorn stderr FD during "
            "long-running sessions (Kevin's 0.1.86 repro)."
        ),
        kind="str",
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
        "MAGI_CHAT_AUDIT_PANEL_ENABLED",
        default=True,
        summary=(
            "Enable the chat Audit panel: per-session policy-enforcement verdict "
            "read endpoint and UI tab (default-ON). Read-only surfacing over "
            "existing observability data; produces no new verdicts. Set =0 to "
            "hide the tab and 404 the endpoint."
        ),
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
    # --- WS1 durable crash-resume substrate (default-OFF) -------------------
    _b(
        "MAGI_DURABLE_LOCAL_WRITES_ENABLED",
        summary=(
            "Master gate for the WS1 durable substrate: create/write the local "
            "durable_checkpoints + plan_ledger tables in the work-queue sqlite. "
            "Local sqlite only; never the hosted DB. OFF is byte-identical."
        ),
    ),
    FlagSpec(
        name="MAGI_DURABLE_MAX_RESUME_ATTEMPTS",
        default=2,
        scope="public",
        stage="stage1",
        summary=(
            "Bound on automatic crash-resume re-entries per (run_id, turn_id) "
            "before the startup sweep gives up and starts fresh (E11/R6)."
        ),
        kind="int",
    ),
    _b(
        "MAGI_DURABLE_CHECKPOINTS_ENABLED",
        summary=(
            "Emit WS1 durable execution checkpoints from the headless tap after "
            "each persisted tool_end and at the turn terminal. Gated additionally "
            "by MAGI_DURABLE_LOCAL_WRITES_ENABLED (the actual sqlite write). OFF "
            "is byte-identical: the tap throws the append uuid away and emits "
            "nothing. Fail-open: an emission error never breaks the turn."
        ),
    ),
    _b(
        "MAGI_DURABLE_STARTUP_RECOVERY_ENABLED",
        summary=(
            "Run the WS1 StartupRecoverySweep at boot: immediately reclaim "
            "background tasks whose owning worker pid is dead (ignoring the "
            "still-valid lease) and dispatch one tick. The guarantee is "
            "AT-LEAST-ONCE; exactly-once for partially-executed side-effecting "
            "tasks needs WS7-outbox. OFF is byte-identical (boot sweep is a no-op)."
        ),
    ),
    _b(
        "MAGI_DURABLE_FOREGROUND_CONTINUATION_ENABLED",
        summary=(
            "Within the WS1 StartupRecoverySweep, additionally perform a "
            "context-only foreground continuation of resumable turn checkpoints "
            "after the background reclaim. For each admissible checkpoint "
            "(non-superseded, resumable, under the resume-attempt bound, with a "
            "present Envelope transcript) the sweep evaluates admissibility and, "
            "ONLY when a live re-drive consumer is wired, re-enters the drive with "
            "the replayed TEXT fold; without a consumer the continuation is inert "
            "(no attempt-counter burn). It does NOT call verify_resume_request "
            "(Correction F). Requires MAGI_DURABLE_STARTUP_RECOVERY_ENABLED and is "
            "excluded from the v1 profile. OFF is byte-identical (no checkpoint is "
            "listed or resumed)."
        ),
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
        name="MAGI_CREDENTIAL_GRANT_TTL_S",
        default=3600,
        scope="public",
        stage="stage2",
        summary=(
            "Lifetime in seconds of an in-chat credential-use approval grant. "
            "After this window the egress proxy re-prompts for the credential "
            "instead of injecting silently forever (a 'remember' approval, when "
            "wired, writes a non-expiring grant). 0 or negative disables expiry "
            "(approve-once-persistent)."
        ),
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
    _b(
        "MAGI_USER_TOOL_PACKS_ENABLED",
        summary=(
            "Discover + merge user-authored tool packs (~/.magi/packs, "
            "<cwd>/.magi/packs) into the CLI tool runtime; never overrides core "
            "tools."
        ),
    ),
    _b(
        "MAGI_USER_VALIDATOR_PACKS_ENABLED",
        summary=(
            "Execute user-authored validator pack impls at the pre-final "
            "evidence gate; a passing verdict observes the required validator "
            "ref, a failing verdict blocks with its detail."
        ),
    ),
    _b(
        "MAGI_USER_EVIDENCE_PACKS_ENABLED",
        summary=(
            "Run user-authored evidence_producer pack runtime emitters at the "
            "pre-final gate; an emitted record's public_ref counts as observed "
            "so it can satisfy a required evidence ref."
        ),
    ),
    _b(
        "MAGI_RECIPE_AS_CODE_ENABLED",
        summary=(
            "Activate code-computed recipe packs: a recipe provides-entry may "
            "carry spec_callable=\"module.path:provide_recipe\" whose callable "
            "returns a RecipePackManifest (or dict). OFF drops such entries at "
            "load time (callable never imported, discovery byte-identical). ON "
            "imports + invokes the callable ONCE at registration, then applies "
            "the SAME external trust validation as declarative recipe specs."
        ),
    ),
    _b(
        "MAGI_PACK_CAPABILITY_ENFORCEMENT_ENABLED",
        summary=(
            "Hand user/untrusted pack impls a RESTRICTED capability set per "
            "primitive type so capability tokens are enforced through the typed "
            "context surface (defense-in-depth, not isolation). OFF: full set, "
            "byte-identical."
        ),
    ),
    _b(
        "MAGI_PACK_SIGNING_REQUIRED",
        stage="stage2",
        summary=(
            "Curated-trust gate (trust model A): when ON, only packs whose "
            "content digest (see magi_agent.packs.signing.compute_pack_digest) "
            "appears in the MAGI_TRUSTED_PACK_DIGESTS allowlist are loaded; an "
            "untrusted user/third-party pack is dropped before load. Bundled "
            "first-party packs (the first-party pack-id prefix) are exempt "
            "(trusted by being bundled). Strict default-OFF: when unset NO digest "
            "is computed and the discover->enabled pipeline is byte-identical to "
            "today."
        ),
    ),
    FlagSpec(
        name="MAGI_TRUSTED_PACK_DIGESTS",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Comma-separated sha256 hex digests of packs the operator has "
            "curated/approved (trust model A allowlist). Only consulted when "
            "MAGI_PACK_SIGNING_REQUIRED is ON; unset means no third-party pack "
            "is trusted."
        ),
        kind="str",
    ),
    _b(
        "MAGI_HOSTED_PACKS_ENABLED",
        stage="stage2",
        scope="hosted",
        summary=(
            "Let the HOSTED serving toolhost load + activate packs from a "
            "configurable per-tenant directory (MAGI_HOSTED_PACKS_DIR), applying "
            "the pack-signing trust gate. Strict default-OFF: when unset the "
            "production serving path is byte-identical (no pack discovery, no "
            "import). Scoped to MAGI_HOSTED_PACKS_DIR only (never ~/.magi or cwd)."
        ),
    ),
    FlagSpec(
        name="MAGI_HOSTED_PACKS_DIR",
        default="",
        scope="hosted",
        stage="stage2",
        summary=(
            "Per-tenant directory the hosted serving path discovers packs under "
            "when MAGI_HOSTED_PACKS_ENABLED is ON. Unset means no hosted packs "
            "are loaded even when the flag is on."
        ),
        kind="str",
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
        "MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED",
        stage="stage2",
        summary=(
            "Apply operator-authored capability_scope custom rules to the "
            "spawned child's resolved toolset (F4): each enabled rule narrows "
            "by denyTools and/or maxPermissionClass (tighten-only — never "
            "widens). Sits between the parent-cap intersection "
            "(MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED) and the per-task "
            "allowedTools/spawn_cap grants (MAGI_SPAWN_RECIPE_CAP_ENABLED). "
            "Requires MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED. With no capability_scope rules "
            "authored, the spawn toolset is byte-identical to before. Strict "
            "default-OFF; fail-open on any customize-store fault so a broken "
            "overrides file never blocks a spawn."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-UX1 Tier 2 lifecycle expansion: activate the two new "
            "audit-only custom_rule gate sites — on_user_prompt_submit "
            "(wired adjacent to BEFORE_SYSTEM_PROMPT in "
            "runtime/message_builder) and on_subagent_stop (wired adjacent "
            "to AFTER_TURN_END in the child runner). Both fan-outs invoke "
            "the existing llm_criterion judge per matching rule and record "
            "audit verdicts only; the surrounding runtime contract is "
            "byte-identical (no mutation of the assembled prompt, no block "
            "of the post-turn emission). Triple-gated with "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault. With no on_user_prompt_submit / "
            "on_subagent_stop rules authored, runtime stays byte-identical "
            "(the new fan-out is a no-op). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-LIFE1 Tier 2 lifecycle expansion (turn boundaries): "
            "activate the two new audit-only custom_rule gate sites — "
            "before_turn_start (wired at the TOP of "
            "runtime/governed_turn.run_governed_turn, BEFORE the engine "
            "stream is started) and after_turn_end (wired in the same "
            "function's ``finally`` block alongside the existing "
            "on_subagent_stop collector). Both fan-outs invoke the "
            "existing llm_criterion judge per matching rule and record "
            "audit verdicts only by default; the backend ``_LEGAL`` "
            "matrix additionally exposes "
            "(llm_criterion, on_subagent_stop, block|ask) so an operator "
            "can author 'subagent must produce a summary'-style rules "
            "whose verdict the parent caller can act on. Triple-gated "
            "with MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault. With no before_turn_start / "
            "after_turn_end rules authored, runtime stays byte-identical "
            "(the new fan-outs are no-ops). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-LIFE2 Tier 2 lifecycle expansion (per-LLM-call boundaries): "
            "activate the two new audit-only custom_rule gate sites — "
            "before_llm_call (wired adjacent to the ADK "
            "before_model_callback boundary inside the runner stream) and "
            "after_llm_call (sibling adjacent to after_model_callback). "
            "Both fan-outs invoke the existing llm_criterion judge per "
            "matching rule and record audit verdicts only; a per-"
            "(session, turn) critic budget (env "
            "MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET, default 3) hard-caps "
            "the combined before+after critic invocations per turn so a "
            "misbehaving rule cannot multiply cost without bound. "
            "Triple-gated with MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault. With no before_llm_call / after_llm_call "
            "rules authored, runtime stays byte-identical (the new fan-outs "
            "are no-ops and the surrounding ADK plugin's per-call work "
            "short-circuits at the helper). Strict default-OFF."
        ),
    ),
    FlagSpec(
        name="MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET",
        default=3,
        scope="public",
        stage="stage2",
        summary=(
            "PR-F-LIFE2 per-turn cost ceiling: maximum combined "
            "before_llm_call + after_llm_call critic invocations within a "
            "single logical turn (per (session_id, turn_id) tuple). When "
            "the budget reaches zero the lifecycle_audit fan-outs short-"
            "circuit to a single budget_exhausted skip record per call "
            "(no critic invocation) so a misbehaving rule cannot multiply "
            "critic cost without bound. Default 3. Read raw from the env "
            "on each turn (no flag_int wrapper needed)."
        ),
        kind="int",
    ),
    _b(
        "MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-LIFE4b Tier 2 lifecycle expansion (task / session "
            "boundary emitters): activate custom_rule gate sites at three "
            "runtime chokepoints that previously had no custom_rule path "
            "— on_task_complete (wired in run_governed_turn's finally "
            "block, fires when the agent declares a multi-turn user task "
            "done via a line-anchored <task_done> marker in the final "
            "assistant text — operator must instruct the agent to emit "
            "the marker as a control signal), "
            "on_session_start (wired by LifecycleSessionControl in the "
            "adk_bridge, fires once per session on the FIRST model call "
            "via a FIFO-bounded per-session 'seen' OrderedDict), and "
            "on_session_end (audit-only; the transport-side emit wire "
            "is honest-degrade in v1 — the wizard exposes the slot so "
            "operators can author rules ahead of the transport wire, "
            "but the runtime never fires until a follow-up adds the "
            "emit). All three fan-outs invoke the existing llm_criterion "
            "judge per matching rule. Triple-gated with "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault. With no on_task_complete / "
            "on_session_start / on_session_end rules authored, runtime "
            "stays byte-identical (the new fan-outs and the "
            "LifecycleSessionControl plugin are no-ops). Strict "
            "default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-LIFE3 Tier 2 lifecycle expansion (four new emitter "
            "slots): activate audit-only custom_rule gate sites at four "
            "runtime chokepoints that previously had no custom_rule path "
            "— before_compaction + after_compaction (wired around "
            "MagiContextCompactionPlugin._apply_tail_trim, covering both "
            "the automatic threshold/real-token decision path and the "
            "manual /compact force path), on_task_checkpoint (wired at "
            "each work-queue task status transition — claimed / "
            "completed / failed — inside WorkQueueDriver.run_once), and "
            "on_artifact_created (wired immediately after a successful "
            "artifact_provider.write_artifact ok-status branch inside "
            "FileDeliveryBoundary.execute). All four fan-outs invoke the "
            "existing llm_criterion judge per matching rule and record "
            "audit verdicts only; the surrounding runtime contract is "
            "byte-identical (no mutation of compaction output, no "
            "interference with task dispatch, no rewrite of the written "
            "artifact). Triple-gated with MAGI_CUSTOMIZE_VERIFICATION_ENABLED "
            "+ MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault. With no before_compaction / "
            "after_compaction / on_task_checkpoint / on_artifact_created "
            "rules authored, runtime stays byte-identical (the new fan-outs "
            "are no-ops). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-MUT1: activate the ``prompt_injection`` custom_rule kind. "
            "When ON the runtime applies enabled prompt_injection rules at "
            "two lifecycle slots: ``before_tool_use`` (append a value to a "
            "chosen tool argument key — e.g. always append '--dry-run' to "
            "shell_exec) wired in magi_agent/facades.py:execute_tool_with_hooks "
            "after the BEFORE_TOOL_USE hook block branch; and "
            "``on_user_prompt_submit`` (append a value as a new system-prompt "
            "section) wired alongside the F-UX1 audit fan-out in "
            "magi_agent/runtime/governed_turn.run_governed_turn. v1 is "
            "append-only (mode=replace is deferred to v2 with an admin-tier "
            "flag) and caps value at 4000 chars. Triple-gated with "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault so a broken overrides file never breaks a "
            "turn. With no prompt_injection rules authored, runtime stays "
            "byte-identical (the new wires are a no-op). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-EXEC1: activate the ``shell_command`` custom_rule kind. "
            "When ON the runtime applies enabled shell_command rules at 11 "
            "lifecycle slots: ``before_tool_use`` (block honored when the "
            "script exits non-zero; wired in magi_agent/facades.py:"
            "execute_tool_with_hooks after the F-MUT1/F-MUT2 mutator "
            "consumers); ``after_tool_use`` (audit-only — dispatch already "
            "returned; same facades wire); plus 9 lifecycle_audit fan-out "
            "helpers covering pre_final (block honored), "
            "on_user_prompt_submit, on_subagent_stop, before_turn_start, "
            "after_turn_end, before_compaction, after_compaction, "
            "on_task_checkpoint, and on_artifact_created (all audit-only). "
            "Subprocess execution uses magi_agent.customize.shell_runner."
            "run_shell_payload with whitelisted env (PATH/HOME/LANG/LC_ALL/"
            "USER/TZ + operator-declared env_vars), bounded timeout "
            "[1, 600] seconds, and start_new_session+SIGKILL group-kill on "
            "timeout. A per-(session, turn) budget "
            "(MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET, default 5) maintained by "
            "LifecycleShellCommandControl hard-caps the combined spawn "
            "count per turn so a misbehaving rule cannot fan out across "
            "slots. Triple-gated with MAGI_CUSTOMIZE_VERIFICATION_ENABLED "
            "+ MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open everywhere. "
            "Self-hosted only — hosted serve activation requires a "
            "separate admin-tier flag (deferred to v2). With no "
            "shell_command rules authored, runtime stays byte-identical "
            "(the new wires are a no-op). Strict default-OFF."
        ),
    ),
    FlagSpec(
        name="MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET",
        default=5,
        scope="public",
        stage="stage2",
        summary=(
            "PR-F-EXEC1 per-turn shell-command cost ceiling: maximum "
            "combined shell_command subprocess spawns across ALL 11 "
            "lifecycle slots within a single logical turn (per "
            "(session_id, turn_id) tuple). When the budget reaches zero "
            "the lifecycle_audit shell fan-outs short-circuit to a single "
            "budget_exhausted skip record per call (no subprocess spawn) "
            "so a misbehaving rule cannot multiply shell cost without "
            "bound. Default 5. Read raw from the env on each turn (no "
            "flag_int wrapper needed). PR-F-EXEC2 reuses the SAME counter "
            "for ``shell_check`` condition-kind invocations so an operator "
            "authoring a mix of action + condition shell rules still gets "
            "one shared cost ceiling per turn."
        ),
        kind="int",
    ),
    _b(
        "MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-EXEC2: activate the ``shell_check`` custom_rule condition "
            "kind. When ON the runtime applies enabled shell_check rules at "
            "two gate slots (``pre_final`` and ``before_tool_use``) as a "
            "deterministic-shaped verdict source: the operator-authored "
            "script reads a context envelope from stdin (JSON: lifecycle, "
            "tool_name?, tool_args?, draft_excerpt?) and emits one of two "
            "shapes on stdout — preferred ``{passed: bool, reason?: str}`` "
            "JSON or fallback exit-code (0 = passed, non-zero = failed). "
            "Subprocess execution shares :mod:`magi_agent.customize.shell_runner` "
            "with F-EXEC1 (same whitelisted env, bounded timeout, "
            "start_new_session + SIGKILL group-kill on timeout). The "
            "per-(session, turn) budget counter "
            "(MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET, default 5) is SHARED with "
            "F-EXEC1 shell_command via "
            ":func:`magi_agent.adk_bridge.lifecycle_shell_command_control"
            ".shell_budget_for` so a turn that fires 3 shell_command spawns "
            "+ 3 shell_check spawns hits the cap at the 6th invocation "
            "regardless of kind. Fail-open everywhere: any runner "
            "exception / unparseable stdout / missing context returns "
            "``passed=True`` (audit-only honest-degrade — a condition that "
            "cannot evaluate must never block a turn). Triple-gated with "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED. With no shell_check "
            "rules authored, runtime stays byte-identical (the new wire "
            "is a no-op). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-MUT2: activate the ``output_rewrite`` custom_rule kind. "
            "When ON the runtime applies enabled output_rewrite rules at the "
            "``after_tool_use`` lifecycle slot, rewriting the dispatched "
            "ToolResult's output text BEFORE the model reads it. v1 ships a "
            "single ``redact`` mode (re.sub(pattern, replacement, text)) with "
            "optional toolMatch include/exclude filters; ``summarize`` and "
            "``replace`` modes are deferred to v2 with an admin-tier flag. "
            "Wired in magi_agent/facades.py:execute_tool_with_hooks after the "
            "AFTER_TOOL_USE hook's typed replace consumer (parallel to the "
            "F-MUT1 BEFORE_TOOL_USE consumer). Triple-gated with "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED; fail-open on any "
            "customize-store fault so a broken overrides file never breaks a "
            "turn. With no output_rewrite rules authored, runtime stays "
            "byte-identical (the new wires are a no-op). Strict default-OFF."
        ),
    ),
    _b(
        "MAGI_CUSTOMIZE_RUNTIME_FIELDS_ENDPOINT_ENABLED",
        stage="stage2",
        summary=(
            "PR-F-UX2 (F8 core): expose the read-only "
            "GET /v1/app/customize/runtime-fields endpoint that returns the "
            "set of runtime variables the wizard's chip picker renders above "
            "regex / contentMatch / llm_criterion / SHACL inputs, per "
            "(lifecycle, condition, tool?) tuple. Derivation is pure "
            "(no I/O, no LLM): tool_input.* expands ToolManifest.input_schema, "
            "evidence:<type>.fields.* reads the F2 _BUILTIN_FIELD_HINTS table, "
            "and context-free vars (session_id, turn_id, ...) are hard-coded "
            "per lifecycle from the runtime gate signatures. Endpoint is "
            "registration-time only, never on the live turn hot path, and "
            "fail-open (unknown tuples return empty fields rather than 5xx). "
            "Strict default-OFF; lab opts in via LAB_EXPERIMENTAL_FLAGS so a "
            "fresh install and hosted serve do not expose the surface until "
            "the operator explicitly enables it."
        ),
    ),
    _pb(
        "MAGI_CREDENTIAL_AWARENESS_ENABLED",
        stage="stage2",
        summary=(
            "Inject a redacted summary of registered Agent Vault credentials into "
            "the system prompt each turn so the agent can acknowledge that a "
            "credential EXISTS (service / auth scheme / approval requirement) "
            "without ever seeing the secret value (the broker injects it on "
            "egress). Profile-aware default-ON (full runtime profile; OFF under "
            "safe/eval). With no registered credentials this is byte-identical "
            "(the block is empty)."
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
    _pb(
        "MAGI_CUSTOMIZE_NL_INTERACTIVE_ENABLED",
        summary=(
            "Enable the POST /v1/app/customize/custom-rules/compile-interactive "
            "endpoint: a conversational multi-turn variant of the one-shot "
            "/custom-rules/compile route. The dashboard's Policies tab uses "
            "this to drive a chat-style policy builder where the operator "
            "describes a rule in plain English, the LLM asks 1-2 clarifying "
            "questions per turn, and the draft IR fills in live until the "
            "runtime validator accepts it. Registration-time only — never on "
            "the runtime hot path. Requires a configured provider/key; "
            "fail-open to a canonical-question fallback when no model is "
            "available. Default-ON under the full / lab profile, OFF under "
            "safe / eval so a key-less benchmark host stays quiet."
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
        "MAGI_CUSTOMIZE_NL_INTERVIEW_MODE_ENABLED",
        summary=(
            "PR-F-UX6: turn the NL compose surface from a one-shot parser into "
            "an interview-driven policy architect. When ON AND the input is "
            "underspecified, the compiler runs ``discover_intent`` to build a "
            "structured intent map and asks 1-3 focused questions (each with "
            "an 'expects' tag — evidence_ref / verifier_ref / field / "
            "tool_name / lifecycle / scope / value / freeform — and an "
            "optional inventory the frontend renders as chip pickers). Once "
            "the intent is resolved the compiler runs "
            "``propose_primitive_or_hybrid`` and may return a HYBRID "
            "composition (multiple primitives sharing a logical groupId) — "
            "e.g. regex pre-filter + LLM critic for AWS-key audits. With the "
            "flag OFF or when the NL is already well-formed, the legacy "
            "one-shot compile path is preserved byte-identically. "
            "Registration-time only; fail-open when no model is available. "
            "Strict default-OFF; lab opts in via LAB_EXPERIMENTAL_FLAGS."
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
    _b(
        "MAGI_ONBOARDING_WIZARD_ENABLED",
        summary=(
            "OSS self-host first-run onboarding wizard. When ON and no model "
            "provider is yet configured, the dashboard bootstrap reports "
            "``setup.needed=true`` so the UI can guide the operator through "
            "picking a provider and entering an API key (persisted to "
            "~/.magi/config.toml via the PUT /v1/app/config route). "
            "With a provider already configured, or with this flag OFF, the "
            "bootstrap is behaviorally unchanged (``setup.needed`` is false); an "
            "additive ``setup`` key is always present and existing consumers "
            "ignore it. Strict default-OFF; self-host only."
        ),
        scope="public",
        stage="stage2",
    ),
    _b(
        "MAGI_CUSTOMIZE_BUDGETS_ENABLED",
        summary=(
            "PR-F7 cost-vocabulary applier: project operator-authored Customize "
            "``verification.budgets`` (maxToolCallsPerTurn / maxStepsBrakeHard / "
            "loopGuardHardThreshold) onto the live MAGI_* env at turn entry via "
            "``setdefault`` so an explicit operator env (k8s / shell export / "
            "dogfood profile) always wins. Triple-gated with "
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED + "
            "MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED. With no budgets authored the "
            "runtime is byte-identical (the applier is a no-op). Strict "
            "default-OFF; the proper ``budget_constraint`` primitive (scope/"
            "turn-type aware) will subsume this in a future series."
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
    # --- TUI surface knobs (I-4 batch 6) ------------------------------------
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
    # --- Vault admin URL + forced recipe (I-4 batch 7) ----------------------
    FlagSpec(
        name="MAGI_VAULT_ADMIN_URL",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "External vault admin URL (when set, the credentials admin "
            "path delegates to a hosted vault instead of the local "
            "sidecar). Empty keeps the local vault sidecar overlay."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_FORCE_RECIPE",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Pin a recipe pack id for every CLI turn — reuses the "
            "compiler's ``explicitRecipeSelection`` path. Unset / blank "
            "keeps automatic selection (byte-identical to today)."
        ),
        kind="str",
    ),
    # --- Observability knobs (I-4 batch) ------------------------------------
    # ``observability/config.ObservabilityConfig.from_env`` resolves these
    # six int knobs via a local ``_int_env`` helper that read
    # ``os.environ`` directly. Registering them lets the helper delegate
    # to ``flag_int`` and removes the last raw env access in the module.
    FlagSpec(
        name="MAGI_OBS_RETENTION_DAYS",
        default=7,
        scope="public",
        stage="stage1",
        summary="Observability event-store retention horizon (days).",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_OBS_MAX_EVENTS",
        default=200_000,
        scope="public",
        stage="stage1",
        summary="Maximum events kept in the observability store before pruning.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_OBS_HEALTH_INTERVAL_S",
        default=5,
        scope="public",
        stage="stage1",
        summary="Seconds between observability health snapshots.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_OBS_MISSION_INTERVAL_S",
        default=30,
        scope="public",
        stage="stage1",
        summary="Seconds between observability mission snapshots.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_OBS_CHANNEL_INTERVAL_S",
        default=10,
        scope="public",
        stage="stage1",
        summary="Seconds between observability channel snapshots.",
        kind="int",
    ),
    FlagSpec(
        name="MAGI_OBS_REPLAY_BUFFER",
        default=200,
        scope="public",
        stage="stage1",
        summary="Observability event replay buffer size.",
        kind="int",
    ),
    # --- Skill curator (I-4 batch 9, on main) ------------------------------
    FlagSpec(
        name="MAGI_SKILL_CURATOR_STALE_DAYS",
        default=30,
        scope="public",
        stage="stage1",
        summary=(
            "Days since last skill use after which a learned skill is "
            "considered stale and surfaced for pruning (default 30)."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_SKILL_CURATOR_INTERVAL_HOURS",
        default="168.0",
        scope="public",
        stage="stage1",
        summary=(
            "Hours between skill-curator runs, parsed as a float "
            "(default 168.0 = 7 days)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_SKILL_CURATOR_IDLE_THRESHOLD_SECONDS",
        default="3600.0",
        scope="public",
        stage="stage1",
        summary=(
            "Seconds of agent idle time before the curator considers a "
            "tick, parsed as a float (default 3600.0 = 1 hour)."
        ),
        kind="str",
    ),
    # --- Scheduler runtime knobs (I-4 batch 12) ----------------------------
    _b(
        "MAGI_OC_CRON_ACTIVE",
        summary=(
            "Operator-set signal that the legacy OC cron daemon is "
            "currently active. When ON the OSS scheduler's transition "
            "guard short-circuits ticks as ``oc_cron_conflict`` to "
            "avoid double-firing jobs."
        ),
    ),
    _b(
        "MAGI_SCHEDULER_KILL_SWITCH_ENABLED",
        summary=(
            "Force the scheduler executor into shadow mode regardless of "
            "per-bot config — emergency kill-switch for runaway jobs."
        ),
    ),
    FlagSpec(
        name="MAGI_SCHEDULER_LOCK_DIR",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Override directory for scheduler advisory lock files. Empty "
            "uses ``~/.magi/scheduler/``. ``~`` is expanded and the path "
            "is confined to a safe parent at resolve time."
        ),
        kind="str",
    ),
    # --- Prompt cache (I-4 batch 12) ---------------------------------------
    _b(
        "MAGI_PROMPT_CACHE_ENABLED",
        summary=(
            "Emit prompt-cache metrics from the prompt-build path. "
            "Default-OFF; ON adds bookkeeping per request."
        ),
    ),
    FlagSpec(
        name="MAGI_PROMPT_CACHE_PROVIDER",
        default="auto",
        scope="public",
        stage="stage1",
        summary=(
            "Provider id to report in prompt-cache metrics. Default "
            "``auto`` picks per-request based on the resolved model."
        ),
        kind="str",
    ),
    # --- adk_bridge runtime knobs (I-4 batch 10) ----------------------------
    FlagSpec(
        name="MAGI_ADK_STREAMING",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "ADK streaming mode. Default-ON; explicit ``0``/``false``/"
            "``no``/``off`` disables. Unset / blank → ON. ``str`` kind "
            "because the deny-set is wider than ``flag_bool``'s strict "
            "truthy convention."
        ),
        kind="str",
    ),
    _b(
        "MAGI_SESSION_PERSISTENCE_ENABLED",
        summary=(
            "Enable the SQLite-backed ADK session store. Default-OFF; "
            "sessions live in process memory."
        ),
    ),
    FlagSpec(
        name="MAGI_DEFERRED_TOOL_THRESHOLD",
        default=30,
        scope="public",
        stage="stage1",
        summary=(
            "Number of tools above which the deferred-load path activates "
            "(only meaningful when ``MAGI_DEFERRED_TOOLS_ENABLED`` is ON; "
            "default 30)."
        ),
        kind="int",
    ),
    # --- External hooks framework (I-4 batch 11) ----------------------------
    _b(
        "MAGI_EXTERNAL_HOOKS_ENABLED",
        summary=(
            "Master switch for the external hooks framework "
            "(LLM-classified + HTTP webhook tap callbacks). Default-OFF."
        ),
    ),
    FlagSpec(
        name="MAGI_LLM_HOOKS_ENABLED",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Sub-switch for LLM-classified hook executors. **Default-ON** "
            "when unset / empty; only an explicit non-truthy value "
            "(anything outside ``{1, true, yes}``) disables. ``str`` "
            "kind because the default-ON-when-unset semantics differ "
            "from ``flag_bool``'s strict default-OFF."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_LLM_HOOK_CLASSIFIER_MODEL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Override the LLM classifier model for the hook executor. "
            "Empty falls back to the per-hook ``classifier_model`` "
            "context value."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_HOOK_HTTP_VERIFY_TLS",
        default="true",
        scope="public",
        stage="stage1",
        summary=(
            "Whether HTTP hook executors verify the upstream TLS cert. "
            "Default ``true``; only the literal ``false`` disables "
            "verification. ``str`` kind because the default-TRUE-when-unset "
            "semantics + literal-only-disable differ from "
            "``flag_bool``'s strict default-OFF + truthy-set."
        ),
        kind="str",
    ),
    _b(
        "MAGI_HOOK_ALLOW_INTERNAL_URLS",
        summary=(
            "Allow hook URLs to point at internal / private network "
            "addresses. Default-OFF (SSRF guard); only enable when "
            "the operator trusts the configured hook URLs."
        ),
    ),
    # --- Gateway / watchers config (I-4 batch 13) ---------------------------
    FlagSpec(
        name="MAGI_SCHEDULER_READINESS_EXECUTION_MODE",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Execution mode for the scheduler readiness gate "
            "(consumed by ``gateway/watchers``). Empty defers to the "
            "watcher's resolved default."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_SCHEDULER_DB_PATH",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Explicit path to the scheduler persistence DB. Empty uses "
            "``<MAGI_STATE_DIR>/scheduler/jobs.db``."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_SCHEDULER_OWNER_DIGEST",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Owner-identity digest to embed on scheduler claims. Empty "
            "uses the runtime-resolved default."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_WORK_QUEUE_CLAIMER",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Claimer id used on work-queue lease acquisitions. Empty "
            "uses the runtime-resolved default."
        ),
        kind="str",
    ),
    # --- Credentials store paths (I-4 batch 14) -----------------------------
    FlagSpec(
        name="MAGI_CREDENTIALS",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Explicit path to the credentials.json store. Empty falls "
            "back to ``<MAGI_CONFIG>'s dir>/credentials.json`` or "
            "``~/.magi/credentials.json``."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_CREDENTIAL_APPROVALS",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Explicit path to the credential approvals.json store. "
            "Empty falls back to ``<MAGI_CONFIG>'s "
            "dir>/credential_approvals.json`` or "
            "``~/.magi/credential_approvals.json``."
        ),
        kind="str",
    ),
    # --- Misc knobs (I-4 batch 15) -----------------------------------------
    FlagSpec(
        name="MAGI_CUSTOMIZE",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Explicit path to the customize.json store. Empty falls "
            "back to ``<MAGI_CONFIG>'s dir>/customize.json`` or "
            "``~/.magi/customize.json``."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_OBS_HOME",
        default="",
        scope="public",
        stage="stage2",
        summary=(
            "Override for the observability home directory. Empty uses "
            "``<cwd>/.openmagi``."
        ),
        kind="str",
    ),
    _b(
        "MAGI_LEDGER_ORCHESTRATOR_ENABLED",
        summary=(
            "Enable the ledger-orchestrator recipe seam. Default-OFF "
            "(GAIA benchmark code)."
        ),
    ),
    _b(
        "MAGI_EXECUTION_TRACE",
        summary=(
            "Enable telemetry execution-trace context capture. "
            "Default-OFF; ON adds trace bookkeeping per turn."
        ),
    ),
    _b(
        "MAGI_PROMPT_TRANSFORM_HOOKS_ENABLED",
        summary=(
            "Run runtime/message_builder's prompt-transform hooks "
            "around each turn's prompt build. Default-OFF."
        ),
    ),
    # --- Shadow stream-event limit (I-4 batch 16) ---------------------------
    FlagSpec(
        name="MAGI_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT",
        default=0,
        scope="public",
        stage="stage1",
        summary=(
            "Maximum text events streamed per selected-full-toolhost "
            "turn. ``0`` (default) defers to the in-module "
            "``_DEFAULT_SELECTED_FULL_TOOLHOST_TEXT_EVENT_LIMIT``; values "
            "below the manual-tool floor are clamped up at the call site."
        ),
        kind="int",
    ),
    # --- Split-line additions (I-4 batch 17) --------------------------------
    FlagSpec(
        name="MAGI_STREAM_FALLBACK_MODEL",
        default="claude-haiku-4-5-20251001",
        scope="public",
        stage="stage1",
        summary=(
            "Model id used by ``runtime/stream_fallback`` when a primary "
            "stream is reclassified as unrecoverable. Snapshotted at "
            "import time."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Hosted gate5b shadow-generation provider label override "
            "(e.g. ``anthropic``, ``openai``). Empty falls back to the "
            "model-derived family. Hosted-only (excluded from the public "
            "env-reference)."
        ),
        kind="str",
    ),
    # I-1: tool-reflection / tool-schema-feedback enable knobs (hermes mech-1).
    # Strict default-OFF; per their existing docstrings they intentionally do
    # NOT follow ``_runtime_feature_enabled`` profile-default-ON semantics so
    # benchmark/eval profiles can opt in explicitly.
    _b(
        "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED",
        summary=(
            "Convert a raising tool (except FileEdit/PatchApply, which keep "
            "their specialized edit-retry handler) into a model-visible "
            "corrective tool_result with retry guidance + per-invocation "
            "attempt budget instead of killing the whole turn. Default-OFF."
        ),
    ),
    _b(
        "MAGI_TOOL_SCHEMA_FEEDBACK_ENABLED",
        summary=(
            "Enrich a dispatcher result with errorCode "
            "``tool_input_schema_invalid`` with plain-text missing/unknown "
            "argument NAMES (vocabulary the model already sees; argument "
            "VALUES are never surfaced) plus hermes-style retry guidance, "
            "under a per-invocation attempt budget. Default-OFF."
        ),
    ),
    # WS9 PR9a: MCP resilience primitive (per-attempt timeout / bounded
    # reconnect / per-server circuit breaker / auth-not-retried). Strict
    # default-OFF (NOT ``_runtime_feature_enabled``): profile-independent so an
    # eval/benchmark or a bare ``MAGI_RUNTIME_PROFILE=full`` does not silently
    # enable it unless a profile/overlay dict explicitly sets ``=1``. Mirrors
    # the ``MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED`` precedent above.
    _b(
        "MAGI_MCP_RESILIENCE_ENABLED",
        summary=(
            "Wrap MCP provider tool calls in a reusable resilience primitive: a "
            "per-attempt call timeout, bounded reconnect with exponential "
            "backoff, a per-server circuit breaker, and non-retryable auth "
            "handling that surfaces a model-visible reconnect signal. When OFF "
            "the call boundary is byte-identical to today. Default-OFF."
        ),
    ),
    # I-1: register the seven 1-liner ``parse_*_*`` flags so the typed
    # registry inventories them and ``flag_bool`` is the single read path.
    # Each kept strictly default-OFF per its function docstring's existing
    # "byte-identical to main when OFF" contract; profile semantics
    # intentionally NOT applied (eval/lab profiles opt in via their env seeds).
    _b(
        "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED",
        summary=(
            "Hosted gate5b serve-path live sub-agents flag (AND-ed with the "
            "live child-runner master gate to reconstruct "
            "``transport.live_subagents_serve_enabled``). Default-OFF."
        ),
    ),
    _b(
        "MAGI_EVAL_AUTONOMY_ENABLED",
        summary=(
            "Eval-profile autonomy seam: opts the eval profile into "
            "looser-permission autonomous loops via "
            "``EVAL_RUNTIME_ENV_DEFAULTS``. Default-OFF."
        ),
    ),
    _b(
        "MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED",
        summary=(
            "Eval-profile zero-edit re-prompt guard: when a coding turn ends "
            "with no file mutations the engine fires a single 'apply it' "
            "re-invocation. Default-OFF; eval profile opts in via "
            "``EVAL_RUNTIME_ENV_DEFAULTS``."
        ),
    ),
    _b(
        "MAGI_RECIPE_DEFAULT_PACKS_EXPANDED",
        summary=(
            "When ON, expand the default-selected first-party pack set to "
            "include additional ``openmagi.evidence`` etc. Default-OFF "
            "preserves the minimal default selection."
        ),
    ),
    _b(
        "MAGI_PLAN_ACT_GATE_ENABLED",
        summary=(
            "Live ``plan_gate -> plan_act_switch -> delegation`` chain "
            "activation. Default-OFF leaves the chain inert and "
            "byte-identical to main."
        ),
    ),
    _b(
        "MAGI_PLAN_MODE_TOOLS_ENABLED",
        summary=(
            "Plan-mode read-only toolset activation. Default-OFF keeps the "
            "full toolset on every turn regardless of declared mode."
        ),
    ),
    _b(
        "MAGI_COMPUTER_TOOL_ENABLED",
        summary=(
            "Live desktop GUI ComputerTool (cua-driver-backed) activation. "
            "Default-OFF; macOS-only; opt-in per the rollout under "
            "``[[project-magi-computer-use]]``."
        ),
    ),
    # I-1: register the two transport/streaming knobs so the ``_truthy_env``
    # call sites can route through ``flag_bool``. Strict default-OFF preserves
    # ``_truthy_env``'s "missing/empty → False" semantics byte-identically.
    _b(
        "MAGI_STREAMING_CHAT",
        summary=(
            "Hosted ``/v1/chat/stream`` SSE route activation. Default-OFF; "
            "the legacy completions endpoint stays the only chat surface "
            "when OFF."
        ),
    ),
    _b(
        "MAGI_STREAM_THINKING",
        summary=(
            "Pass-through thinking-delta events to the public SSE surface "
            "(``transport/sse``, ``shadow/gate5b4c3_live_runner_boundary``, "
            "``adk_bridge/event_adapter``). Default-OFF clips thinking text "
            "before it reaches any external consumer."
        ),
    ),
    # I-1: hosted gate-readiness ENABLED knobs. All strict default-OFF
    # ``_b(...)`` (kind ``bool``) so the hosted operator must opt in
    # explicitly per gate; matches the existing ``_is_true`` semantics
    # (missing/empty → False) byte-identically. Hosted-only (excluded
    # from the public env-reference via the ``hosted`` scope marker on
    # the FlagSpec; default ``_b`` is ``public``, so these use explicit
    # ``FlagSpec(scope="hosted", ...)`` rather than the ``_b`` helper).
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE2_READINESS_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate2 readiness ladder activation (sandbox harness + "
            "ready-state probes). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE3_READINESS_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate3 readiness ladder activation (replay harness + "
            "evidence-record probes). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE4_READINESS_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate4 readiness ladder activation (consumer + dry-run "
            "boundary probes). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5_READINESS_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5 readiness ladder activation (live-shadow runner + "
            "preflight probes). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate7 readiness ladder activation (post-runner audit "
            "probes). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate8 selected-authority routing activation (selected "
            "bot/owner digests). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted memory adapter knobs. All strict default-OFF; each is
    # currently a "not approved" guard — the parser raises when set
    # truthy. Registering as ``_b`` (strict ``bool``) keeps the
    # ``_is_true`` semantics byte-identical (missing/empty → False).
    FlagSpec(
        name="CORE_AGENT_PYTHON_MEMORY_PROMPT_PROJECTION",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted memory adapter prompt-projection seam (NOT APPROVED — "
            "parser raises when truthy). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_MEMORY_LIVE_PROVIDER_CALLS",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted memory adapter live provider calls seam (NOT APPROVED "
            "— parser raises when truthy). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_MEMORY_ADK_SERVICE_ATTACHMENT",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted memory adapter ADK-service attachment seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted toolhost attachment knobs.
    FlagSpec(
        name="CORE_AGENT_PYTHON_ADK_TOOLHOST_ATTACH",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted ADK toolhost attachment master switch. Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_TOOLHOST_LIVE_TOOL_MUTATION",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted toolhost live-tool mutation seam. Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted security-posture + context-continuity master switches.
    FlagSpec(
        name="CORE_AGENT_PYTHON_SECURITY_POSTURE_PREFLIGHT",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted security-posture preflight check activation. "
            "Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted context-continuity ladder activation. Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted gate3a recorded-replay master switches.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE3A_RECORDED_REPLAY",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate3a recorded-replay bundle ingestion. Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE3A_ALLOW_MODEL_CALLS",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate3a recorded-replay allow-model-calls seam. "
            "Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted runtime-authority request flags. Both are operator
    # request signals that the runtime accompanies with a mandatory
    # gate authority. Strict default-OFF — missing/empty stays at the
    # ``PythonRuntimeAuthorityConfig()`` (no user-visible / no canary)
    # default the original ``_is_true`` form produced.
    FlagSpec(
        name="CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime authority request: allow user-visible output. "
            "Must be paired with ``CORE_AGENT_PYTHON_CANARY_ROUTING`` and "
            "a sanctioning gate authority (gate8 or gate5b user-visible "
            "canary). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CANARY_ROUTING",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime authority request: allow canary routing. Must "
            "be paired with ``CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT`` and "
            "a sanctioning gate authority. Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted gate5b kill switches.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_KILL_SWITCH",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b global kill switch. When truthy, the user-"
            "visible canary authority check raises. Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b user-visible canary kill switch. When truthy, "
            "the gate5b user-visible canary authority check raises. "
            "Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted gate-config ENABLED knobs consumed by ``transport/chat_shared``
    # builders. All strict default-OFF — missing/empty stays at the existing
    # ``enabled=False`` default ``_is_true(env.get(...))`` produced.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b user-visible canary chat-route activation. "
            "Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate1a read-only toolhost activation (route- and "
            "kill-switch gated). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate2 sandbox-canary workspace chat-route activation. "
            "Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_SELECTED_PROVIDER_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate2 sandbox-canary selected-provider routing seam. "
            "Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted security-posture "false-only" guards. Each flag must
    # remain unset/falsy or ``parse_python_security_posture_env`` raises
    # ``RuntimeEnvError("...is not approved")``. Strict default-OFF
    # ``bool`` so ``flag_bool`` is byte-identical to the prior
    # ``_is_true(env.get(name))`` loop body.
    FlagSpec(
        name="CORE_AGENT_PYTHON_SECURITY_EXTERNAL_SURFACE_DISPATCH",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted security-posture external-surface-dispatch seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_SECURITY_CREDENTIAL_BROKER_ATTACHMENT",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted security-posture credential-broker attachment seam "
            "(NOT APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_SECURITY_CONTEXT_GUARD_BLOCK_MODE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted security-posture context-guard block-mode seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_SECURITY_SUPPLY_CHAIN_STARTUP_BANNER",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted security-posture supply-chain startup-banner seam "
            "(NOT APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted context-continuity "false-only" guards. Same shape as
    # the security-posture guards above — each must remain unset/falsy
    # or ``parse_python_context_continuity_env`` raises.
    FlagSpec(
        name="CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PRODUCTION_AUTHORITY",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted context-continuity production-authority seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_TRANSCRIPT_WRITE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted context-continuity transcript-write seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SSE_WRITE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted context-continuity SSE-write seam (NOT APPROVED — "
            "parser raises when truthy). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_DB_WRITE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted context-continuity DB-write seam (NOT APPROVED — "
            "parser raises when truthy). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_EVIDENCE_VERIFIED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted context-continuity canary-evidence-verified seam "
            "(NOT APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    # I-1: hosted runtime-authority "false-only" guards. Each must
    # remain unset/falsy or ``parse_python_runtime_authority_env``
    # raises ``RuntimeEnvError("...is not approved")``. Strict
    # default-OFF ``bool`` so ``flag_bool`` is byte-identical to the
    # prior ``_is_true(env.get(name))`` loop body.
    FlagSpec(
        name="CORE_AGENT_PYTHON_TRANSCRIPT_WRITE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority transcript-write seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_SSE_WRITE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority SSE-write seam (NOT APPROVED — "
            "parser raises when truthy). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CHANNEL_DELIVERY",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority channel-delivery seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_DB_WRITE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority DB-write seam (NOT APPROVED — "
            "parser raises when truthy). Default-OFF; hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_WORKSPACE_MUTATION",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority workspace-mutation seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_CHILD_EXECUTION",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority child-execution seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_MISSION_RUNTIME",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority mission-runtime seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_EVIDENCE_BLOCK_MODE",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted runtime-authority evidence block-mode seam (NOT "
            "APPROVED — parser raises when truthy). Default-OFF; "
            "hosted-only."
        ),
        kind="bool",
    ),
    # I-1: local vault seam operator knobs (``credentials_admin/vault_local``).
    # Three master bools register strict default-OFF; the URL string was
    # already registered. All operator-visible — the env-reference
    # generator should list them.
    _b(
        "MAGI_VAULT_ADMIN_ENABLED",
        summary=(
            "Master switch for the local credential vault seam. Default-OFF "
            "leaves ``register_credential`` inert (returns "
            "``{'disabled': True}``); when ON the external vault path "
            "requires ``MAGI_VAULT_ADMIN_URL`` and the native local vault "
            "path requires ``MAGI_LOCAL_VAULT_ENABLED``."
        ),
    ),
    _b(
        "MAGI_LOCAL_VAULT_ENABLED",
        summary=(
            "Activate the native local credential vault backend. Default-OFF "
            "at the library level; the local serve / dashboard bootstrap "
            "flips it on via ``setdefault`` in ``runtime/local_defaults.py``. "
            "Forced OFF when ``MAGI_VAULT_ADMIN_URL`` is configured (the "
            "external HTTP backend then takes precedence)."
        ),
    ),
    _b(
        "MAGI_LOCAL_VAULT_PROXY_ENABLED",
        summary=(
            "Activate the local credential-injecting forward proxy. "
            "Default-OFF; requires the native local vault to also be "
            "active (``MAGI_LOCAL_VAULT_ENABLED``) and is forced OFF when "
            "``MAGI_VAULT_ADMIN_URL`` is configured."
        ),
    ),
    # I-1: egress-proxy operator knobs (``egress_proxy/config.py``
    # ``EgressProxyConfig.from_env``). All operator-visible — the
    # public env-reference generator should list them.
    _b(
        "MAGI_EGRESS_PROXY_ENABLED",
        summary=(
            "Force the egress proxy on at the runtime boundary. Requires "
            "``MAGI_EGRESS_PROXY_URL`` + ``MAGI_EGRESS_PROXY_CA_CERT_PATH`` "
            "when ON or ``EgressProxyConfig.validate`` raises. Default-OFF."
        ),
    ),
    FlagSpec(
        name="MAGI_EGRESS_PROXY_URL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Egress proxy origin URL (``http://`` or ``https://``, no "
            "path / query / fragment / embedded credentials). Required "
            "when ``MAGI_EGRESS_PROXY_ENABLED`` is truthy."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_EGRESS_PROXY_AUTH",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Egress proxy bearer/basic auth credential (kept out of "
            "``EgressProxyConfig.__repr__`` per its ``field(repr=False)`` "
            "marker — the runtime value MUST NOT be echoed in discovery "
            "dumps; this registration only inventories the knob name)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_EGRESS_PROXY_CA_CERT_PATH",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "PEM CA bundle path the proxy presents. Required when "
            "``MAGI_EGRESS_PROXY_ENABLED`` is truthy; "
            "``EgressProxyConfig.validate`` raises if missing or "
            "unreadable."
        ),
        kind="str",
    ),
    # I-1: CLI provider-selection knobs. Both operator-visible — the
    # env-reference generator should list them so users discover the
    # ``MAGI_MODEL`` / ``MAGI_PROVIDER`` overrides without grepping
    # ``cli/providers``.
    FlagSpec(
        name="MAGI_MODEL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Bare provider-native model id override (e.g. ``claude-opus-4-8`` "
            "or ``gpt-5.5``). When set, overrides every provider's catalog "
            "default; empty/unset keeps each provider's built-in default."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_PROVIDER",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Provider override (e.g. ``anthropic`` / ``openai`` / ``google`` / "
            "``fireworks`` / ``openrouter``). When set, picks the named "
            "provider's key + model; empty/unset auto-detects from available "
            "provider keys. Unknown provider raises ``UnknownProviderError``."
        ),
        kind="str",
    ),
    # I-1: vault directory override consumed by both the local vault
    # backend (``credentials_admin/local_vault.resolve_vault_dir``) and
    # the sidecar admin server (``credentials_admin/vault_server``).
    # Operator-visible; empty/unset falls through to the next layer.
    FlagSpec(
        name="MAGI_VAULT_DIR",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Override the local credential vault directory. Resolution "
            "order: ``MAGI_VAULT_DIR`` > ``<MAGI_CONFIG parent>/vault`` "
            "> ``~/.magi/vault``. The sidecar vault server prefers "
            "``AGENT_VAULT_STORE_DIR`` over this knob when both are set."
        ),
        kind="str",
    ),
    # I-1: composio integration knobs (``composio/config.resolve_composio_config``).
    # Every parser handles empty-string identically to None, so ``flag_str``
    # default ``""`` is byte-identical to the prior ``env.get(NAME)`` →
    # ``None`` chain.
    FlagSpec(
        name="MAGI_COMPOSIO_ENABLED",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Composio integration mode (``on`` / ``off`` / ``auto``). "
            "Default ``auto`` activates when ``COMPOSIO_API_KEY`` is set and "
            "the ``composio`` package is importable; ``on`` requires both "
            "and fails-loud when missing; ``off`` disables unconditionally."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_COMPOSIO_CREDENTIAL_SOURCE",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Composio credential source (``env`` / ``hosted`` / ``platform``). "
            "Default auto-selects: a local ``COMPOSIO_API_KEY`` uses ``env`` "
            "(operator's own key); otherwise a free platform token "
            "(``MAGI_PLATFORM_API_KEY``) opts into ``platform`` mode, which "
            "brokers tool calls through the platform broker (``MAGI_PLATFORM_"
            "BASE_URL``, master key held server-side) so no Composio key is "
            "needed. ``hosted`` brokers through an in-process platform master "
            "key (Open Magi Pro+ pods)."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_COMPOSIO_ENTITY_ID",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Composio entity id override. Empty/unset falls back to the "
            "runtime-derived entity (``<USER_ID>:<BOT_ID>``) and finally "
            "to ``default``."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_COMPOSIO_TOOLKITS",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Comma-separated Composio toolkit allowlist (e.g. "
            "``gmail,google_calendar,slack``). Empty/unset enables every "
            "toolkit Composio surfaces."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_COMPOSIO_MCP_URL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Composio MCP server URL override (rarely needed; defaults to "
            "Composio's hosted endpoint). Must be a valid HTTP(S) URL when "
            "set."
        ),
        kind="str",
    ),
    # I-1: hosted gate1a egress-correlation knobs (``evidence/observed_egress``
    # + ``evidence/gate1a_egress_correlation``). All ``scope="hosted"``;
    # empty/unset falls through to the no-op
    # ``NoObservedEgressEvidenceProvider``.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_EGRESS_PROXY_URL",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Override the gate1a egress proxy URL. Falls back to "
            "``HTTPS_PROXY`` / ``https_proxy`` when unset. Default-"
            "empty; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate1a egress evidence source. Only "
            "``egress_proxy_telemetry`` activates the live telemetry "
            "provider; empty/unset/other values keep the no-op provider. "
            "Hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_EGRESS_TELEMETRY_PATH",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Path to the egress proxy telemetry artifact. Required when "
            "``CORE_AGENT_PYTHON_GATE1A_EGRESS_EVIDENCE_SOURCE`` is "
            "``egress_proxy_telemetry``; empty/unset short-circuits the "
            "live provider to the no-op. Hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_EGRESS_CORRELATION_MODE",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate1a egress correlation mode. Compared exactly against the "
            "internal ``_LIVE_EGRESS_CORRELATION_MODE`` constant to flip "
            "``correlation_source_configured`` on the live provider. "
            "Hosted-only."
        ),
        kind="str",
    ),
    # I-1: read-quality tools max-lines budget. Operator-visible; matches the
    # existing ``readMaxLines`` advertised on the hosted tools-config surface.
    FlagSpec(
        name="MAGI_READ_QUALITY_MAX_LINES",
        default=2000,
        scope="public",
        stage="stage1",
        summary=(
            "Maximum number of lines the read-quality tool surface reports "
            "per file. Default 2000; malformed (non-integer) values fall "
            "back to the default."
        ),
        kind="int",
    ),
    # the gate config builders. All ``scope="hosted", default=""``;
    # missing falls through to the existing ``.strip()`` / ``_csv_values``
    # defaults.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENVIRONMENT",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate8 selected-authority environment label. Default-"
            "empty; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_ENV_ALLOWLIST",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Comma-separated allowlist of environments gate8 will accept. "
            "Default-empty disables the environment check; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b user-visible canary environment label. "
            "Default-empty; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Comma-separated allowlist of environments gate5b user-"
            "visible canary will accept. Default-empty; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ENV_ALLOWLIST",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Comma-separated allowlist of environments the gate1a read-"
            "only toolhost will accept. Default-empty; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENVIRONMENT",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate2 sandbox-canary environment label. Default-"
            "empty; hosted-only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ENV_ALLOWLIST",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Comma-separated allowlist of environments gate2 sandbox-"
            "canary will accept. Default-empty; hosted-only."
        ),
        kind="str",
    ),
    # I-1 batch 21: gate kill-switch / route-attachment / FULL_TOOLHOST
    # master switch FlagSpecs. Default-TRUE switches are deliberate —
    # the prior raw shape ``_is_true(env.get(NAME, "1"))`` (and the
    # ``_env_bool_default_true(env.get(NAME))`` shape for the gate8
    # selected-authority path) both resolve to True when unset. Migrating
    # them with ``default=True`` keeps ``flag_bool(..., env=env)`` byte-
    # identical: ``None`` returns ``spec.default`` (True), every set
    # value goes through ``_is_true``.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE8_SELECTED_AUTHORITY_KILL_SWITCH",
        default=True,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate8 selected-authority kill switch. Default-TRUE; "
            "operators flip OFF to disable the authority check. Hosted-"
            "only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_KILL_SWITCH",
        default=True,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate1a read-only toolhost kill switch. Default-TRUE; "
            "operators flip OFF to disable the kill-switch gate. Hosted-"
            "only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_ROUTE_ATTACHMENT",
        default=True,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate1a read-only toolhost route attachment. Default-"
            "TRUE; operators flip OFF to detach the route. Hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED",
        default=False,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b full-toolhost master switch. Default-OFF; "
            "operators flip ON to expose the gate5b full toolhost route. "
            "Hosted-only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_KILL_SWITCH",
        default=True,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b full-toolhost kill switch. Default-TRUE; "
            "operators flip OFF to disable the kill-switch gate. Hosted-"
            "only."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ROUTE_ATTACHMENT",
        default=True,
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate5b full-toolhost route attachment. Default-TRUE; "
            "operators flip OFF to detach the route. Hosted-only."
        ),
        kind="bool",
    ),
    # I-1 batch 24: eval-deadline countdown nudge knob.
    FlagSpec(
        name="MAGI_EVAL_DEADLINE_SECONDS",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Float-seconds total budget for the current eval/run. When "
            "set (>0), the toolhost surfaces a one-time nudge at "
            "progressive threshold crossings. Stored as ``str`` (parsed "
            "to ``float`` at read time) so the empty / invalid / "
            "non-positive cases all collapse to ``no deadline``."
        ),
        kind="str",
    ),
    # I-1 batch 23: bootstrap port + agent-require-env knob.
    FlagSpec(
        name="CORE_AGENT_PORT",
        default=8080,
        scope="public",
        stage="stage1",
        summary=(
            "Server bootstrap port. Hosted infra sets this; the "
            "``--port`` flag still wins when explicitly passed."
        ),
        kind="int",
    ),
    FlagSpec(
        name="MAGI_SERVE_HOST",
        default="0.0.0.0",
        scope="public",
        stage="stage1",
        summary=(
            "Server bootstrap bind host. Defaults to ``0.0.0.0`` "
            "(all interfaces) so hosted infra is byte-identical; the "
            "``--host`` flag still wins when explicitly passed. The "
            "desktop shell passes ``127.0.0.1`` to bind loopback only."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_AGENT_REQUIRE_ENV",
        default=False,
        scope="public",
        stage="stage1",
        summary=(
            "When truthy, the bootstrap refuses to fall back to local "
            "dev defaults on a ``RuntimeEnvError`` and re-raises "
            "instead. Default-OFF so local ``magi serve`` runs without "
            "a fully-populated hosted env."
        ),
        kind="bool",
    ),
    # I-1 batch 20: gate1a + gate5b full-toolhost integer caps + gate2
    # sandbox-canary root path. Defaults match the prior inline fallback
    # constants in ``chat_shared`` / ``gate2_sandbox_canary``.
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_CALLS_PER_TURN",
        default=8,
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate1a read-only toolhost per-turn tool-call cap. Hosted-"
            "only; default 8."
        ),
        kind="int",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_PER_TOOL_BYTES",
        default=4096,
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate1a read-only toolhost per-tool output byte cap. Hosted-"
            "only; default 4096."
        ),
        kind="int",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE1A_READONLY_TOOLS_MAX_AGGREGATE_BYTES",
        default=16384,
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate1a read-only toolhost aggregate-output byte cap across "
            "the turn. Hosted-only; default 16384."
        ),
        kind="int",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_MAX_CALLS_PER_TURN",
        default=16,
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate5b full-toolhost per-turn tool-call cap. Hosted-only; "
            "default 16."
        ),
        kind="int",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_MAX_PER_TOOL_BYTES",
        default=8192,
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate5b full-toolhost per-tool output byte cap. Hosted-only; "
            "default 8192."
        ),
        kind="int",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_COMMAND_TIMEOUT_MS",
        default=5000,
        scope="hosted",
        stage="stage1",
        summary=(
            "Gate5b full-toolhost shell-command timeout (milliseconds). "
            "Hosted-only; default 5000."
        ),
        kind="int",
    ),
    FlagSpec(
        name="CORE_AGENT_PYTHON_GATE2_SANDBOX_CANARY_ROOT",
        default="",
        scope="hosted",
        stage="stage1",
        summary=(
            "Hosted gate2 sandbox-canary root path. Default-empty "
            "collapses to ``None`` at both consumers (the dataclass and "
            "the durable-evidence helper). Hosted-only."
        ),
        kind="str",
    ),
    # I-1 batch 27: file-delivery workspace subdir overrides. Both
    # default-empty; the consumer collapses ``""`` to a built-in
    # ``.magi/deliveries/{artifacts,outbox}`` default via the local
    # ``if not artifact_subdir:`` / ``if not outbox_subdir:`` guards,
    # so a typed FlagSpec default cannot just pin the const directly
    # without churning the existing guard.
    FlagSpec(
        name="MAGI_FILE_DELIVERY_ARTIFACT_DIR",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Override for the file-delivery artifact subdirectory "
            "(within the resolved workspace). Default-empty; consumer "
            "falls back to ``.magi/deliveries/artifacts``."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_FILE_DELIVERY_OUTBOX_DIR",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Override for the file-delivery outbox subdirectory "
            "(within the resolved workspace). Default-empty; consumer "
            "falls back to ``.magi/deliveries/outbox``."
        ),
        kind="str",
    ),
    # I-1 batch 25 (REDO): live web-acquisition provider toggles +
    # credentials. The original PR #1071 squash silently dropped the
    # entire batch — recovered here on a fresh branch.
    FlagSpec(
        name="MAGI_LIVE_WEB_ACQUISITION_ENABLED",
        default=False,
        scope="public",
        stage="stage1",
        summary=(
            "Master switch for the live web-acquisition stack (platform "
            "endpoint + insane.fetch + jina reader). Default-OFF; the "
            "research/citation tools fall back to local-only when this "
            "is unset."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="MAGI_LIVE_WEB_ACQUISITION_KILL_SWITCH",
        default=False,
        scope="public",
        stage="stage1",
        summary=(
            "Kill switch for the live web-acquisition stack. When "
            "truthy, ``live_web_acquisition_active`` returns ``False`` "
            "regardless of the master enable. Default-OFF (alive)."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="MAGI_WEB_PROVIDER_ROUTER_ENABLED",
        default=False,
        scope="public",
        stage="stage1",
        summary=(
            "Enable the multi-provider router that picks search vs. "
            "fetch backends per request. Default-OFF; the first viable "
            "provider in the resolved order wins when this is unset."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="MAGI_INSANE_FETCH_ENABLED",
        default=False,
        scope="public",
        stage="stage1",
        summary=(
            "Enable the ``insane.fetch`` (``curl_cffi``-based) WAF-"
            "bypass fetch provider. Default-OFF; loaded lazily on first "
            "ON observation."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="MAGI_JINA_READER_ENABLED",
        default=False,
        scope="public",
        stage="stage1",
        summary=(
            "Enable the Jina Reader fallback fetch/reader provider. "
            "Default-OFF; loaded lazily and ordered after the platform "
            "+ insane.fetch providers."
        ),
        kind="bool",
    ),
    FlagSpec(
        name="MAGI_PLATFORM_BASE_URL",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "Base URL of the hosted Magi platform search/fetch endpoint. "
            "When set together with ``MAGI_PLATFORM_API_KEY``, the "
            "platform provider becomes the primary live-web backend."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_PLATFORM_API_KEY",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "API key for the hosted Magi platform search/fetch endpoint. "
            "Required together with ``MAGI_PLATFORM_BASE_URL`` to enable "
            "the platform provider; left empty for self-host."
        ),
        kind="str",
    ),
    FlagSpec(
        name="MAGI_JINA_API_KEY",
        default="",
        scope="public",
        stage="stage1",
        summary=(
            "API key for the Jina Reader fallback provider. Optional; "
            "Jina Reader works without a key but with stricter quotas."
        ),
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
    """Read a registered ``str`` flag, falling back to the registry default.

    I-11 (Option A): the ``spec.default`` field is a union
    ``str | bool | int | None`` shared across every flag kind. Narrow it
    here with an ``isinstance`` check after the kind validator so the
    return type is statically correct and the historical
    ``# type: ignore[return-value]`` disappears. A defensively
    mis-registered str-kind default that is not actually a ``str``
    surfaces as ``None`` (the caller treats it the same as "unset"),
    matching the prior behavioural contract.
    """

    spec = get_flag(name)
    if spec.kind != "str":
        raise TypeError(f"flag {name!r} has kind {spec.kind!r}, not 'str'")
    raw = _resolve_env(env).get(name)
    if raw is None:
        return spec.default if isinstance(spec.default, str) else None
    return raw


def flag_int(name: str, *, env: Mapping[str, str] | None = None) -> int | None:
    """Read a registered ``int`` flag; invalid values fall back to the default.

    I-11 (Option A): same narrowing pattern as :func:`flag_str` — the
    union default is reduced to ``int | None`` here via ``isinstance``
    so the two historical ``# type: ignore[return-value]`` lines
    disappear. The ``isinstance(int)`` check intentionally rejects
    ``bool`` (a subclass of ``int``) so a mis-registered ``bool``
    default for an int flag surfaces as ``None`` rather than ``True/False``.
    """

    spec = get_flag(name)
    if spec.kind != "int":
        raise TypeError(f"flag {name!r} has kind {spec.kind!r}, not 'int'")
    raw = _resolve_env(env).get(name)
    default = spec.default if isinstance(spec.default, int) and not isinstance(
        spec.default, bool
    ) else None
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return default
