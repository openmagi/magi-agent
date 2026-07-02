"""Runner routing and policy-assembly helpers, pure move out of engine/driver.py (PR-G2).

These module-level symbols form the local OSS runner routing concern:
``RunnerPolicyAssembly`` plus the phase-classification, intent-binding, tool
name resolution, sentinel and attachment helpers the driver threads through.
Bodies are moved verbatim (only ``_classify_policy_phase_with_softening`` gains
a function-local import to reach the task-type helpers that stay on the driver,
avoiding an import cycle). The driver re-imports every name so existing import
paths and ``is`` identity are preserved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, init=False)
class RunnerPolicyAssembly:
    """Local OSS runner policy assembled from first-party recipes.

    This object is intentionally public-metadata shaped. It can be threaded into
    harness state and emitted as a runtime event, but it cannot grant production
    write authority.
    """

    model_provider: str
    model_label: str
    selected_pack_ids: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    required_validators: tuple[str, ...]
    missing_evidence_action: str
    repair_policy: Mapping[str, object]
    attachment_flags: Mapping[str, bool]
    task_profile: Mapping[str, object]
    phase_routing: Mapping[str, object]
    provider_intents: tuple[str, ...]
    tool_intents: tuple[str, ...]
    channel_intents: tuple[str, ...]
    artifact_intents: tuple[str, ...]
    scheduler_intents: tuple[str, ...]

    def __init__(
        self,
        *,
        modelProvider: str | None = None,
        model_provider: str | None = None,
        modelLabel: str | None = None,
        model_label: str | None = None,
        selectedPackIds: tuple[str, ...] | list[str] = (),
        selected_pack_ids: tuple[str, ...] | list[str] = (),
        evidenceRequirements: tuple[str, ...] | list[str] = (),
        evidence_requirements: tuple[str, ...] | list[str] = (),
        requiredValidators: tuple[str, ...] | list[str] = (),
        required_validators: tuple[str, ...] | list[str] = (),
        missingEvidenceAction: str | None = None,
        missing_evidence_action: str | None = None,
        repairPolicy: Mapping[str, object] | None = None,
        repair_policy: Mapping[str, object] | None = None,
        attachmentFlags: Mapping[str, bool] | None = None,
        attachment_flags: Mapping[str, bool] | None = None,
        taskProfile: Mapping[str, object] | None = None,
        task_profile: Mapping[str, object] | None = None,
        phaseRouting: Mapping[str, object] | None = None,
        phase_routing: Mapping[str, object] | None = None,
        providerIntents: tuple[str, ...] | list[str] = (),
        provider_intents: tuple[str, ...] | list[str] = (),
        toolIntents: tuple[str, ...] | list[str] = (),
        tool_intents: tuple[str, ...] | list[str] = (),
        channelIntents: tuple[str, ...] | list[str] = (),
        channel_intents: tuple[str, ...] | list[str] = (),
        artifactIntents: tuple[str, ...] | list[str] = (),
        artifact_intents: tuple[str, ...] | list[str] = (),
        schedulerIntents: tuple[str, ...] | list[str] = (),
        scheduler_intents: tuple[str, ...] | list[str] = (),
    ) -> None:
        object.__setattr__(
            self,
            "model_provider",
            _non_empty_str(model_provider or modelProvider, "local"),
        )
        object.__setattr__(
            self,
            "model_label",
            _non_empty_str(model_label or modelLabel, "local-stub"),
        )
        object.__setattr__(
            self,
            "selected_pack_ids",
            _str_tuple(selected_pack_ids or selectedPackIds),
        )
        object.__setattr__(
            self,
            "evidence_requirements",
            _str_tuple(evidence_requirements or evidenceRequirements),
        )
        object.__setattr__(
            self,
            "required_validators",
            _str_tuple(required_validators or requiredValidators),
        )
        object.__setattr__(
            self,
            "missing_evidence_action",
            _non_empty_str(missing_evidence_action or missingEvidenceAction, "audit"),
        )
        object.__setattr__(
            self,
            "repair_policy",
            dict(repair_policy or repairPolicy or {}),
        )
        object.__setattr__(
            self,
            "attachment_flags",
            _authority_safe_attachment_flags(attachment_flags or attachmentFlags or {}),
        )
        object.__setattr__(
            self,
            "task_profile",
            dict(task_profile or taskProfile or {}),
        )
        object.__setattr__(
            self,
            "phase_routing",
            dict(phase_routing or phaseRouting or {}),
        )
        object.__setattr__(
            self,
            "provider_intents",
            _str_tuple(provider_intents or providerIntents),
        )
        object.__setattr__(
            self,
            "tool_intents",
            _str_tuple(tool_intents or toolIntents),
        )
        object.__setattr__(
            self,
            "channel_intents",
            _str_tuple(channel_intents or channelIntents),
        )
        object.__setattr__(
            self,
            "artifact_intents",
            _str_tuple(artifact_intents or artifactIntents),
        )
        object.__setattr__(
            self,
            "scheduler_intents",
            _str_tuple(scheduler_intents or schedulerIntents),
        )

    def to_public_payload(self) -> dict[str, object]:
        return {
            "modelProvider": self.model_provider,
            "modelLabel": self.model_label,
            "selectedPackIds": list(self.selected_pack_ids),
            "evidenceRequirements": list(self.evidence_requirements),
            "requiredValidators": list(self.required_validators),
            "missingEvidenceAction": self.missing_evidence_action,
            "repairPolicy": dict(self.repair_policy),
            "attachmentFlags": dict(self.attachment_flags),
            "taskProfile": dict(self.task_profile),
            "phaseRouting": dict(self.phase_routing),
            "providerIntents": list(self.provider_intents),
            "toolIntents": list(self.tool_intents),
            "channelIntents": list(self.channel_intents),
            "artifactIntents": list(self.artifact_intents),
            "schedulerIntents": list(self.scheduler_intents),
        }

    def phase_route_decision(self) -> dict[str, object] | None:
        """Distill the materialized phase routing into a consumable decision.

        ``phase_routing`` is the raw ``PhaseRoutingPlan.model_dump(by_alias=True)``
        threaded in by the recipe materializer; until D1 nothing read it. This
        normalizes it into the routing *hints* the engine/CLI surfaces actually
        consume (per-phase model selection, escalation policy, denial state).
        Returns ``None`` when no routing was materialized, so the OFF / stub path
        stays byte-identical.
        """

        routing = self.phase_routing
        if not routing:
            return None

        phase_routes = _routing_field(routing, "phaseRoutes", "phase_routes") or {}
        phase_models: dict[str, dict[str, str]] = {}
        escalation_policies: dict[str, str] = {}
        verifier_tiers: dict[str, str] = {}
        denied_phases: list[str] = []
        requires_stronger_verifier = False
        if isinstance(phase_routes, Mapping):
            for phase, route in phase_routes.items():
                if not isinstance(route, Mapping):
                    continue
                phase_key = str(phase)
                provider = route.get("provider")
                model = route.get("model")
                tier = route.get("tier")
                if (
                    isinstance(provider, str)
                    and isinstance(model, str)
                    and isinstance(tier, str)
                ):
                    phase_models[phase_key] = {
                        "provider": provider,
                        "model": model,
                        "tier": tier,
                    }
                policy = _routing_field(route, "escalationPolicy", "escalation_policy")
                if isinstance(policy, str) and policy:
                    escalation_policies[phase_key] = policy
                    if policy == "bounded_stronger_verifier":
                        requires_stronger_verifier = True
                verifier_tier = _routing_field(route, "verifierTier", "verifier_tier")
                if isinstance(verifier_tier, str) and verifier_tier:
                    verifier_tiers[phase_key] = verifier_tier
                if bool(_routing_field(route, "routeDenied", "route_denied")):
                    denied_phases.append(phase_key)

        max_sota = _routing_field(routing, "maxSotaEscalations", "max_sota_escalations")
        denial_reason = _routing_field(routing, "denialReason", "denial_reason")
        return {
            "routeDenied": bool(_routing_field(routing, "routeDenied", "route_denied")),
            "denialReason": denial_reason if isinstance(denial_reason, str) else None,
            "fallbackToTypeScript": bool(
                _routing_field(routing, "fallbackToTypeScript", "fallback_to_typescript")
            ),
            "maxSotaEscalations": (
                int(max_sota) if isinstance(max_sota, int) and not isinstance(max_sota, bool) else 0
            ),
            "phaseModels": phase_models,
            "escalationPolicies": escalation_policies,
            "verifierTiers": verifier_tiers,
            "deniedPhases": tuple(denied_phases),
            "requiresStrongerVerifier": requires_stronger_verifier,
        }


_CODING_PROMPT_MARKERS = frozenset(
    {
        "apply_patch",
        "bash",
        "bug",
        "build",
        "code",
        "commit",
        "compile",
        "debug",
        "diff",
        "edit",
        "file",
        "fix",
        "grep",
        "implement",
        "lint",
        "patch",
        "pytest",
        "refactor",
        "repo",
        "script",
        "test",
        "typescript",
        "코드",
        "파일",
        "테스트",
        "수정",
        "고쳐",
        "구현",
        "패치",
        "버그",
        "리팩터",
        "커밋",
    }
)


class _GateAttachment:
    """Restoration handle for a gate ``before_tool_callback`` attachment."""

    __slots__ = ("agent", "original")

    def __init__(self, *, agent: object, original: object) -> None:
        self.agent = agent
        self.original = original


class _RunnerRouteAttachment:
    """Restoration handle for a local runner route attachment."""

    __slots__ = (
        "agent",
        "runner",
        "original_tools",
        "original_instruction",
        "original_agent_route",
        "original_runner_route",
    )

    def __init__(
        self,
        *,
        agent: object,
        runner: object,
        original_tools: object,
        original_instruction: object,
        original_agent_route: object,
        original_runner_route: object,
    ) -> None:
        self.agent = agent
        self.runner = runner
        self.original_tools = original_tools
        self.original_instruction = original_instruction
        self.original_agent_route = original_agent_route
        self.original_runner_route = original_runner_route


class _Sentinel:
    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self._name}>"


_EXHAUSTED = _Sentinel("adk_stream_exhausted")
_CANCELLED = _Sentinel("adk_stream_cancelled")
_MISSING = _Sentinel("missing")
_RUNNER_POLICY_ROUTING_ENV = "MAGI_RUNNER_POLICY_ROUTING_ENABLED"
_RUNNER_POLICY_ROUTE_BLOCKING_ENV = "MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED"
_RECIPE_INTENT_BINDING_ENV = "MAGI_RECIPE_INTENT_BINDING_ENABLED"
_CODING_PHASES = frozenset(
    {"code_search", "patch_planning", "patch_generation", "test_interpretation"}
)
_LOCAL_READONLY_TOOL_NAMES = frozenset(
    {
        "ArtifactList",
        "ArtifactRead",
        "Calculation",
        "Clock",
        "FileRead",
        "GitDiff",
        "Glob",
        "Grep",
    }
)
_TOOL_INTENT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "tool:file.read": ("FileRead",),
    "tool:test.run": ("TestRun",),
    "tool:git.diff": ("GitDiff",),
    "tool:FileDeliver": ("FileDeliver",),
    "tool:FileSend": ("FileSend",),
    "tool:ChannelDispatcher": ("ChannelDispatcher",),
    "tool:NotifyUser": ("NotifyUser",),
}


def _non_empty_str(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _str_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,) if values else ()
    if not isinstance(values, list | tuple):
        return ()
    return tuple(str(value) for value in values if str(value))


def _routing_field(source: object, alias: str, snake: str) -> object:
    """Read a phase-routing field by its alias or snake_case key.

    The materialized plan is dumped ``by_alias=True`` (camelCase), but a
    hand-built plan or a future ``mode="python"`` dump may use snake_case.
    """

    if not isinstance(source, Mapping):
        return None
    if alias in source:
        return source[alias]
    return source.get(snake)


def _authority_safe_attachment_flags(flags: Mapping[str, bool]) -> dict[str, bool]:
    safe = {str(key): bool(value) for key, value in flags.items()}
    safe["productionWriteAllowed"] = False
    safe["userVisibleOutputAllowed"] = False
    return safe


def _runner_policy_routing_enabled() -> bool:
    """Whether runner-policy phase routing emits and attaches safe routes.

    The code-level default stays OFF. Installed/local full-runtime profiles and
    hosted canary profiles should enable this explicitly in their config/env.

    Reads through the canonical registry (I-2 PR A) so the truthy convention
    lives in exactly one place: explicit ``"1"/"true"/"yes"/"on"`` enables;
    any other value — including unset and unknown values like ``"enabled"``
    — keeps the default-OFF authority gate closed.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(_RUNNER_POLICY_ROUTING_ENV)


def _runner_policy_route_blocking_enabled() -> bool:
    """Whether denied materialized routes fail closed before provider calls.

    Route blocking is intentionally NOT part of the default full-runtime profile:
    materialized route denials can be stale or conservative while the configured
    model is still capable of finishing the user turn. By default, route denials
    are emitted as audit metadata and the turn continues on the configured model.
    Operators can explicitly re-arm the older hard-block boundary with this env.

    Reads through the canonical registry (I-2 PR A); strict allowlist semantics.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(_RUNNER_POLICY_ROUTE_BLOCKING_ENV)


def _recipe_intent_binding_enabled() -> bool:
    """Whether emit-only recipe intents bind to hint-level runner effects.

    Stage gate for doc 05 PR-3 (A1-G2). The code-level default stays OFF, so the
    emitted runner-policy route selection is byte-identical to origin/main unless
    an operator opts in. See ``parse_recipe_intent_binding_enabled``.

    Reads through the canonical registry (I-2 PR A); strict allowlist semantics.
    """
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(_RECIPE_INTENT_BINDING_ENV)


def compile_intent_bindings(
    assembly: RunnerPolicyAssembly,
    *,
    enabled: bool,
) -> dict[str, object]:
    """Bind the four emit-only recipe intent families to hint-level effects.

    ``provider_intents`` / ``channel_intents`` / ``artifact_intents`` /
    ``scheduler_intents`` are materialized as public-payload metadata but, unlike
    ``tool_intents``, have no consumer driving a runner effect. This compiles them
    into a hint-level binding payload that downstream seams (model selection,
    channel delivery, pre-final artifact requirements, the 03-always-on
    scheduler) can read.

    The binding is intentionally *hint* level: it never asserts production-write
    authority and never hard-forces a model/channel/provider (open-decision §6-2:
    hint, not force; hard enforcement deferred to 14-controlplane).

    When ``enabled`` is False this returns ``{}`` so the caller's emitted payload
    stays byte-identical to origin/main.
    """
    if not enabled:
        return {}
    bindings: dict[str, object] = {
        "schemaVersion": "magi.recipeIntentBinding.v1",
        "enforcement": "hint",
        "productionWriteAllowed": False,
    }
    if assembly.provider_intents:
        bindings["providerPreferenceHints"] = list(assembly.provider_intents)
    if assembly.channel_intents:
        bindings["channelDeliveryHints"] = list(assembly.channel_intents)
    if assembly.artifact_intents:
        bindings["artifactDeliveryRequirements"] = list(assembly.artifact_intents)
    if assembly.scheduler_intents:
        bindings["schedulerReadinessHints"] = list(assembly.scheduler_intents)
    return bindings


def _phase_routes(phase_routing: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw = phase_routing.get("phaseRoutes") or phase_routing.get("phase_routes")
    if not isinstance(raw, Mapping):
        return {}
    routes: dict[str, Mapping[str, object]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, Mapping):
            routes[key] = value
    return routes


def _select_policy_phase(
    *,
    phases: tuple[str, ...],
    prompt: str,
    harness_state: object | None,
    assembly: RunnerPolicyAssembly,
    phase_routes: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    """E-8: classifier returns a phase whose route is NOT denied when
    ``phase_routes`` is supplied (soft-fail to conversational on denial).

    Back-compat: pre-E-8 callers omit ``phase_routes`` and get the
    legacy "return the keyword phase even if denied" behavior. See
    :func:`_classify_policy_phase_with_softening` for the variant that
    also reports whether soft-fail fired (observability).
    """

    phase, _ = _classify_policy_phase_with_softening(
        phases=phases,
        prompt=prompt,
        harness_state=harness_state,
        assembly=assembly,
        phase_routes=phase_routes,
    )
    return phase


def _classify_policy_phase_with_softening(
    *,
    phases: tuple[str, ...],
    prompt: str,
    harness_state: object | None,
    assembly: RunnerPolicyAssembly,
    phase_routes: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[str, str | None]:
    """E-8: classify + report softening for observability.

    Returns ``(resolved_phase, classified_phase_if_softened)``. When the
    keyword classifier picks a phase whose route is denied,
    ``resolved_phase`` is the conversational fallback and
    ``classified_phase_if_softened`` is the original (denied) phase the
    classifier would have picked pre-E-8. When no softening fires
    (route available, or no ``phase_routes`` supplied), the second
    element is ``None``.
    """
    # The task-type helpers stay on the engine driver (their canonical home is
    # the gate stack, extracted in a later unit). Import them lazily here so
    # this routing module never statically depends on the driver, which
    # re-imports us; the module-load DAG stays acyclic. Behavior is unchanged.
    from magi_agent.engine.driver import (  # noqa: PLC0415
        _CODING_TASK_TYPES,
        _extract_task_types,
        _normalize_task_type,
    )

    phase_set = set(phases)
    # Only the live harness state describes the CURRENT task. ``assembly.task_profile``
    # is the bot's static CAPABILITY superset (every taskType it could ever do — it
    # always contains "coding"), so falling back to it made EVERY turn — including a
    # plain "hi" — classify as coding, select the ``patch_generation`` phase, and
    # (when the routed model lacks coding capability) fail-closed with
    # ``runner_policy_route_denied``. Derive the task from harness_state + the prompt
    # markers below only; with neither signal, fall through to the conversational
    # ``final_answer_drafting`` phase.
    task_types = {
        _normalize_task_type(item)
        for item in (_extract_task_types(harness_state) or ())
    }
    prompt_lower = prompt.lower()

    # E-8 — soft-fail: never return a phase whose route is denied for the
    # current model when a conversational fallback is available. Pre-E-8
    # the classifier could pick a denied phase that then failed-CLOSED as
    # ``runner_policy_route_denied``. Now the classifier filters denied
    # phases out of every short-circuit so the worst case is "answer
    # conversationally" instead of "block the turn". We only soften when
    # the route is *denied* — we never weaken an *enforceable* gate.
    def _route_denied(phase: str) -> bool:
        if phase_routes is None:
            return False
        route = phase_routes.get(phase)
        if not isinstance(route, Mapping):
            return False
        return bool(route.get("routeDenied") or route.get("route_denied"))

    def _available(phase: str) -> bool:
        return phase in phase_set and not _route_denied(phase)

    # Track the keyword classifier's *first* pick — used for the
    # ``phase_misclassification_softened`` observability hint when its
    # route is denied and we soft-fail to conversational.
    classified_pick: str | None = None

    coding_requested = bool(task_types & _CODING_TASK_TYPES) or any(
        marker in prompt_lower for marker in _CODING_PROMPT_MARKERS
    )
    if coding_requested:
        for phase in ("patch_generation", "code_search", "test_interpretation"):
            if phase in phase_set and classified_pick is None:
                classified_pick = phase
            if _available(phase):
                return (phase, None)

    research_requested = bool(
        task_types & {"research", "web-acquisition", "browser-automation"}
    ) or any(marker in prompt_lower for marker in ("research", "source", "cite", "web"))
    if research_requested:
        for phase in ("source_acquisition", "source_extraction"):
            if phase in phase_set and classified_pick is None:
                classified_pick = phase
            if _available(phase):
                return (phase, None)

    if "final_answer_drafting" in phase_set:
        return ("final_answer_drafting", classified_pick)
    if "intent_classification" in phase_set:
        return ("intent_classification", classified_pick)
    return (phases[0], classified_pick)


def _local_tool_names_for_route(
    *,
    runner: object,
    assembly: RunnerPolicyAssembly,
    phase: str,
    route: Mapping[str, object],
) -> tuple[str, ...]:
    available = _available_agent_tool_names(runner)
    if not available:
        return ()

    selected = set(assembly.selected_pack_ids)
    capabilities = set(_str_tuple(route.get("capabilities")))
    coding_route = (
        phase in _CODING_PHASES
        or "coding" in capabilities
        or "openmagi.dev-coding" in selected
    )
    if coding_route:
        return ()

    desired = set(_LOCAL_READONLY_TOOL_NAMES)
    for intent in assembly.tool_intents:
        desired.update(_tool_names_for_intent(intent))

    return tuple(name for name in available if name in desired)


def _available_agent_tool_names(runner: object) -> tuple[str, ...]:
    agent = getattr(runner, "agent", None)
    tools = getattr(agent, "tools", None)
    if not isinstance(tools, list):
        return ()
    return tuple(name for tool in tools if (name := _tool_name(tool)) is not None)


def _tool_name(tool: object) -> str | None:
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


def _tool_names_for_intent(intent: str) -> tuple[str, ...]:
    aliased = _TOOL_INTENT_ALIASES.get(intent)
    if aliased is not None:
        return aliased
    if not intent.startswith("tool:"):
        return ()
    raw = intent.removeprefix("tool:")
    if not raw:
        return ()
    if "." in raw:
        return ()
    return (raw,)


def _restore_attr(target: object, name: str, original: object) -> None:
    try:
        if original is _MISSING:
            delattr(target, name)
        else:
            setattr(target, name, original)
    except Exception:
        pass
