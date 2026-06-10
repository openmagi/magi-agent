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

Important: strict-truthy flags only
-----------------------------------
``flag_bool`` implements the *strict opt-in* semantics (``"1"/"true"/"yes"/"on"``
truthy, everything else — including unset — falsey). It deliberately does NOT
model the profile-aware default-ON behaviour of
``env._runtime_feature_enabled`` (``MAGI_RUNTIME_PROFILE``-sensitive). Those
flags keep their dedicated helper and are out of scope for this reader; mixing
the two semantics into one ``flag_bool`` would silently flip default-ON gates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Literal

# Reuse the single truthy convention already established in env.py rather than
# inventing a new one (the cluster's explicit "no new truthy set" rule).
from .env import _is_true

__all__ = [
    "FlagScope",
    "Stage",
    "FlagKind",
    "FlagSpec",
    "FLAGS",
    "FLAGS_BY_NAME",
    "get_flag",
    "flag_bool",
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

FlagKind = Literal["bool", "str", "int"]


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
        "MAGI_CHANNEL_WORKFLOWS_ENABLED",
        summary="Enable bot-user dynamic channel workflows (classifier-driven).",
    ),
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
    _b(
        "MAGI_BROWSER_TOOL_ENABLED",
        summary="Expose the browser-use autonomous vision BrowserTask tool.",
    ),
    _b(
        "MAGI_FILE_DELIVERY_LIVE_ENABLED",
        summary="Enable the live file-delivery tool (vs receipt-only).",
    ),
    _b(
        "MAGI_CROSS_VERIFY_ENABLED",
        summary="Enable the cross-verification gate over spawned-agent results.",
    ),
    _b(
        "MAGI_DEFERRED_TOOLS_ENABLED",
        summary="Enable deferred (lazily-loaded) tool schemas.",
    ),
    # --- Coding harness -----------------------------------------------------
    _b(
        "MAGI_EDIT_FUZZY_MATCH_ENABLED",
        default=True,
        summary="Use the 9-stage fuzzy-match cascade for FileEdit (default-ON full profile).",
    ),
    _b(
        "MAGI_EDIT_FORMAT_ON_WRITE_ENABLED",
        summary="Run a formatter on files written by the coding harness.",
    ),
    _b(
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        summary="Reflect on failed edits before retrying (coding repair loop).",
    ),
    _b(
        "MAGI_CODING_REPAIR_LOOP_ENABLED",
        summary="Enable the iterative coding repair loop on failing edits.",
    ),
    _b(
        "MAGI_LSP_DIAGNOSTICS_ENABLED",
        summary="Surface LSP diagnostics to the coding harness.",
    ),
    _b(
        "MAGI_RIPGREP_ENABLED",
        summary="Use ripgrep for fast in-repo search when available.",
    ),
    _b(
        "MAGI_APPLY_PATCH_ENABLED",
        summary="Enable the apply-patch tool for multi-file edits.",
    ),
    # --- Evidence / verification gates -------------------------------------
    _b(
        "MAGI_EGRESS_GATE_ENABLED",
        summary="Run the evidence-grounded critic gate before chat egress.",
    ),
    _b(
        "MAGI_SELF_INTROSPECTION_ENABLED",
        default=True,
        summary="Advertise the InspectSelfEvidence tool (default-ON full profile).",
    ),
    _b(
        "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED",
        default=True,
        summary="Build per-turn EvidenceLedger objects (default-ON full profile).",
    ),
    _b(
        "MAGI_EVIDENCE_COMPLETION_GATE_ENABLED",
        summary="Block turn completion when required evidence is missing.",
    ),
    _b(
        "MAGI_DOCUMENT_AUTHORING_COVERAGE",
        summary="Block document turns on failed DocumentCoverage (vs audit-only).",
        scope="public",
    ),
    # --- Resilience controls ------------------------------------------------
    _b(
        "MAGI_LOOP_GUARD_ENABLED",
        summary="Enable the repetition/loop guard brake.",
    ),
    _b(
        "MAGI_ERROR_RECOVERY_ENABLED",
        summary="Enable automatic error-recovery retries.",
    ),
    _b(
        "MAGI_OUTPUT_CONTINUATION_ENABLED",
        summary="Enable automatic continuation of truncated model output.",
    ),
    _b(
        "MAGI_CONTEXT_COMPACTION_ENABLED",
        summary="Compact the working context when the token threshold is hit.",
    ),
    # --- Surfaces -----------------------------------------------------------
    _b(
        "MAGI_CLI_ENABLED",
        default=True,
        summary="Enable the magi CLI surface (headless NDJSON + Textual TUI).",
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
