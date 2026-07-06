"""Runtime ``GoalLoopPolicy`` — the per-turn shape PR-C's clean-break judge
will read to decide whether to keep the agent running until the original
objective is complete (Hermes "Ralph loop" pattern).

PR-A (#835) restored the per-send Goal-mission toggle on the composer.
PR-B (this module + wiring): parses ``goalMode`` from the chat-completions
payload, constructs the runtime policy, and threads it through a per-turn
ContextVar so the engine (PR-C) can read it without signature changes
through four layers of builders.

PR-B is intentionally a NO-OP for the engine. The engine reads the policy in
PR-C and gates the clean-break judge call on it.

Design reference:
  docs/plans/2026-06-21-magi-goal-loop-clean-break-judge-design.md (host repo)
  Section 4.2.2 (policy shape) + Section 5.2 (backend wire).

Naming separation: the older :mod:`magi_agent.harness.goal_loop` module holds
a large pydantic *contract* schema (ownership scopes, opt-out states, spawn
depth policies, ...). That schema is metadata for cross-system audits and is
NOT the runtime shape the engine consumes per-turn. We intentionally keep the
runtime shape minimal here so PR-C can wire it without first solving the
broader scaffold-activation question.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: Default upper bound on clean-break re-invocations within a single goal mission.
#: Mirrors Hermes ``DEFAULT_MAX_TURNS=20`` (the production reference). Tunable
#: per deployment via ``MAGI_GOAL_LOOP_MAX_TURNS``.
DEFAULT_GOAL_LOOP_MAX_TURNS = 20

#: Default upper bound on clean-break re-invocations for the AMBIENT (toggle-off)
#: goal loop, applied to the no-ledger objective-aware judge path only. Kept
#: small and distinct from the mission ceiling because ambient continuation is
#: the finish-the-job baseline, not an explicit "keep going" mission. Tunable per
#: deployment via ``MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS``.
DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS = 3

#: Default number of judge JSON-parse failures tolerated before the engine
#: terminates the goal mission (fail-CLOSED — we do not loop forever on a
#: broken judge model). Mirrors Hermes' bounded fallback.
DEFAULT_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET = 2

#: Default continuation prompt — fed back to the SAME session when the judge
#: returns ``complete=false``. Deliberately generic and short so the model
#: cannot anchor on the wording and re-describe its plan again; the original
#: system prompt + tool catalog do all the heavy lifting (matches Hermes'
#: prefix-cache-preserving design).
DEFAULT_CONTINUATION_TEMPLATE = (
    "The original objective is not yet complete. Continue executing the next "
    "concrete step now, using the available tools. Do not restate the plan or "
    "describe what you will do — just do it."
)

_GOAL_LOOP_MAX_TURNS_ENV = "MAGI_GOAL_LOOP_MAX_TURNS"
_AMBIENT_GOAL_LOOP_MAX_TURNS_ENV = "MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS"
_GOAL_LOOP_JUDGE_PROVIDER_ENV = "MAGI_GOAL_LOOP_JUDGE_PROVIDER"
_GOAL_LOOP_JUDGE_MODEL_ENV = "MAGI_GOAL_LOOP_JUDGE_MODEL"
_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET_ENV = (
    "MAGI_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET"
)


@dataclass(frozen=True)
class GoalLoopPolicy:
    """Per-turn goal-loop policy consumed by the engine's clean-break branch.

    Fields are minimal by design — anything not strictly needed by the judge
    call lives elsewhere (e.g. recipe / persona / model selection comes from
    the request's existing model overlay, not from this policy). The engine
    treats this object as opaque: presence ⇒ goal mode is on for this turn.
    Absence of a per-turn (ContextVar-published) policy no longer implies
    single-turn: under a profile-ON goal loop the driver may synthesize an
    ambient policy at the clean break (design 5.1); it falls back to the prior
    single-turn behavior only when synthesis is inert (loop OFF / child / empty
    objective).
    """

    #: Always ``True`` when this object exists (a falsy ``enabled`` would mean
    #: "policy present but disabled", which is the same as "no policy" and we
    #: collapse that to ``None`` at the factory). Kept explicit so the engine
    #: can defensively recheck without branching on object identity alone.
    enabled: bool
    #: The original user objective — fed to the judge so the model can decide
    #: "is the objective complete?" against the agent's last final-text turn.
    objective: str
    #: Upper bound on clean-break re-invocations within this goal mission.
    max_turns: int
    #: Optional provider override for the judge call (cheap-tier preferred).
    #: ``None`` defers selection to the engine's key-aware fallback at judge
    #: time (chosen so PR-B can ship without making provider decisions yet).
    judge_provider: str | None
    #: Optional model override for the judge call.
    judge_model: str | None
    #: Number of judge JSON-parse failures tolerated before terminating the
    #: goal mission (fail-CLOSED). 0 means terminate on the first parse fail.
    judge_parse_failures_budget: int
    #: Generic continuation prompt re-fed when the judge says not-complete.
    continuation_template: str



def _parse_max_turns(raw: object) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_GOAL_LOOP_MAX_TURNS
    try:
        parsed = int(raw.strip(), 10)
    except (TypeError, ValueError):
        return DEFAULT_GOAL_LOOP_MAX_TURNS
    if parsed <= 0:
        return DEFAULT_GOAL_LOOP_MAX_TURNS
    return parsed


def _parse_ambient_max_turns(raw: object) -> int:
    # Byte-for-byte the same validation/clamp as ``_parse_max_turns`` (empty /
    # non-str / non-int / <= 0 all fall back), the ONLY difference being the
    # ambient default ceiling. Kept as a sibling helper rather than a shared
    # parametrized parser so the mission builder stays untouched by this unit.
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS
    try:
        parsed = int(raw.strip(), 10)
    except (TypeError, ValueError):
        return DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS
    if parsed <= 0:
        return DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS
    return parsed


def _parse_parse_failures_budget(raw: object) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET
    try:
        parsed = int(raw.strip(), 10)
    except (TypeError, ValueError):
        return DEFAULT_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET
    if parsed < 0:
        return DEFAULT_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET
    return parsed


def _clean_optional_str(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def build_goal_loop_policy_from_request(
    *,
    goal_mode_requested: bool,
    objective: str,
    env: Mapping[str, str],
) -> GoalLoopPolicy | None:
    """Construct the MISSION-intensity ``GoalLoopPolicy``, or ``None`` when no
    explicit mission was requested / the loop is disabled.

    This is the explicit-toggle (mission) builder used by transports. Returning
    ``None`` means the transport publishes no per-turn policy; the engine may
    then synthesize an ambient (finish-the-job baseline) policy driver-side
    (design 5.1), so ``None`` here is NOT "single-turn" in general, it just means
    "no explicit mission intensity for this turn". Returns ``None`` in any of:

    * ``goal_mode_requested`` is false (no explicit Goal-mission toggle; the
      ambient baseline is synthesized in the driver, not here).
    * ``MAGI_GOAL_LOOP_ENABLED`` resolves OFF for *env* (the profile-aware
      master gate: ON under the full/lab profile, OFF under the safe-family or
      an explicit ``"0"``).
    * ``objective`` is empty after trimming (nothing meaningful to judge).

    Otherwise returns a populated policy. Provider/model selection for the
    judge call is left optional here so PR-B can ship without making the
    provider decision yet; PR-C resolves it from the deployment's configured
    keys at judge-call time.
    """
    if not goal_mode_requested:
        return None
    # Route through the canonical profile-aware accessor so this call-site
    # agrees with ``config.env.is_goal_loop_enabled`` (the flag is a registered
    # ``profile_bool``). The former inline strict-truthy env read resolved OFF
    # whenever the profile default (ON) was left unset, disagreeing with the
    # rest of the runtime.
    from magi_agent.config.env import is_goal_loop_enabled  # noqa: PLC0415

    if not is_goal_loop_enabled(env):
        return None
    cleaned_objective = (objective or "").strip()
    if not cleaned_objective:
        return None
    return GoalLoopPolicy(
        enabled=True,
        objective=cleaned_objective,
        max_turns=_parse_max_turns(env.get(_GOAL_LOOP_MAX_TURNS_ENV)),
        judge_provider=_clean_optional_str(env.get(_GOAL_LOOP_JUDGE_PROVIDER_ENV)),
        judge_model=_clean_optional_str(env.get(_GOAL_LOOP_JUDGE_MODEL_ENV)),
        judge_parse_failures_budget=_parse_parse_failures_budget(
            env.get(_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET_ENV)
        ),
        continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
    )


def build_ambient_goal_loop_policy(
    *,
    objective: str,
    env: Mapping[str, str],
) -> GoalLoopPolicy | None:
    """Construct the AMBIENT ``GoalLoopPolicy`` for a turn, or ``None`` if off.

    This is the toggle-independent finish-the-job baseline: unlike
    :func:`build_goal_loop_policy_from_request`, there is NO
    ``goal_mode_requested`` gate — ambient is the toggle-OFF path by definition,
    so the composer intensity toggle is never consulted here. Existence is
    governed solely by the profile-aware master flag and the presence of a real
    objective. Returns ``None`` (which the engine treats as "no ambient policy,
    behave exactly as today") when:

    * ``MAGI_GOAL_LOOP_ENABLED`` resolves OFF for *env* (the same profile-aware
      master gate the mission builder uses: ON under the full/lab profile, OFF
      under the safe-family or an explicit ``"0"``).
    * ``objective`` is empty after trimming (a turn with no capturable user text
      behaves exactly as today — the ledger-first SEAM 2 path still covers the
      ledger case).

    Otherwise returns a policy identical in shape to the mission policy, with the
    SOLE difference that ``max_turns`` is the AMBIENT ceiling
    (``MAGI_GOAL_LOOP_AMBIENT_MAX_TURNS``, default
    ``DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS``) rather than the mission ceiling. The
    judge provider/model, parse-failure budget, and continuation template are
    resolved identically to the mission builder so the two policies stay in
    lockstep for every field except the ceiling source.

    Inert until wired by a later unit (driver-side ambient synthesis); nothing
    calls this yet.
    """
    from magi_agent.config.env import is_goal_loop_enabled  # noqa: PLC0415

    if not is_goal_loop_enabled(env):
        return None
    cleaned_objective = (objective or "").strip()
    if not cleaned_objective:
        return None
    return GoalLoopPolicy(
        enabled=True,
        objective=cleaned_objective,
        max_turns=_parse_ambient_max_turns(env.get(_AMBIENT_GOAL_LOOP_MAX_TURNS_ENV)),
        judge_provider=_clean_optional_str(env.get(_GOAL_LOOP_JUDGE_PROVIDER_ENV)),
        judge_model=_clean_optional_str(env.get(_GOAL_LOOP_JUDGE_MODEL_ENV)),
        judge_parse_failures_budget=_parse_parse_failures_budget(
            env.get(_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET_ENV)
        ),
        continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
    )


__all__ = [
    "DEFAULT_GOAL_LOOP_MAX_TURNS",
    "DEFAULT_AMBIENT_GOAL_LOOP_MAX_TURNS",
    "DEFAULT_GOAL_LOOP_JUDGE_PARSE_FAILURES_BUDGET",
    "DEFAULT_CONTINUATION_TEMPLATE",
    "GoalLoopPolicy",
    "build_goal_loop_policy_from_request",
    "build_ambient_goal_loop_policy",
]
