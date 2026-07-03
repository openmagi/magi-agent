"""Pre-final evidence-gate stack helpers, pure move out of engine/driver.py (PR-G3).

These module-level helpers implement the pre-final gate decision surface
(applies-decision, coding-repair evaluation, document-coverage and SHACL
verification, verifier-bus projection, task-type extraction). Bodies are moved
verbatim; the driver re-imports every name so import paths are preserved. The
gate stack depends downward on engine_routing only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from magi_agent.engine.engine_routing import _CODING_PROMPT_MARKERS, RunnerPolicyAssembly


_CODING_TASK_TYPES = frozenset(
    {
        "coding",
        "code",
        "dev-coding",
        "developer",
        "software",
        "workspace",
        "file-edit",
        "patch",
    }
)


_NON_CODING_TASK_TYPES = frozenset(
    {
        "chat",
        "general",
        "conversation",
        "research",
        "readonly",
        "read-only",
        "planning",
        "plan",
    }
)


def _pre_final_gate_applies(
    *,
    assembly: RunnerPolicyAssembly,
    prompt: str,
    harness_state: object | None,
    coding_mutation_observed: bool,
    live_selected_pack_ids: Sequence[str] = (),
) -> bool:
    """Return whether the assembled policy should enforce the final gate.

    The local runner may assemble the dev-coding pack as an available first-party
    policy, but availability is not the same thing as routing every turn through
    a coding verification gate.  The dev-coding verification gate exists to
    confirm that *code mutations* were tested/grounded — so it only has something
    to enforce on a turn that actually mutated a file. A read-only / research
    turn (no file-mutating tool call) produces nothing to verify and must not be
    blocked, even when the prompt classifier would otherwise flag it as coding.
    """

    dev_coding_pack_id = "openmagi.dev-coding"
    base_selected = set(assembly.selected_pack_ids)
    selected = base_selected | set(live_selected_pack_ids)
    if dev_coding_pack_id not in selected:
        return True

    # Mutation-scope: the coding evidence gate only applies to turns that
    # actually changed code. No file mutation ⇒ nothing coding to verify.
    if not coding_mutation_observed:
        # If dev-coding is part of the PROFILE baseline, preserve main's exact
        # behavior: a no-mutation turn produces nothing to verify ⇒ no gate.
        # (When live_selected_pack_ids is empty this branch is always taken,
        # so the OFF-path stays byte-identical to main.)
        if dev_coding_pack_id in base_selected:
            return False
        # dev-coding arrived PURELY via live selection. Its (mutation-scoped)
        # coding obligation has nothing to verify on a no-mutation turn, but we
        # must NOT suppress the gate — that would drop the non-coding profile
        # baseline obligations. Defer to the baseline's own applies-decision
        # (i.e. what the gate would decide without the live dev-coding pack).
        return _pre_final_gate_applies(
            assembly=assembly,
            prompt=prompt,
            harness_state=harness_state,
            coding_mutation_observed=coding_mutation_observed,
            live_selected_pack_ids=(),
        )

    task_types = _extract_task_types(harness_state)
    if task_types:
        normalized = {_normalize_task_type(item) for item in task_types}
        if normalized & _CODING_TASK_TYPES:
            return True
        if normalized & _NON_CODING_TASK_TYPES:
            return False

    normalized_prompt = prompt.lower()
    return any(marker in normalized_prompt for marker in _CODING_PROMPT_MARKERS)


def _build_coding_repair_decision_payload(
    repair_policy: Mapping[str, object],
    *,
    attempt_count: int = 0,
    latest_test_evidence: Mapping[str, object] | None = None,
    is_coding_turn: bool = True,
) -> dict[str, object]:
    from magi_agent.coding.repair_loop import (
        CodingRepairLoopConfig,
        CodingRepairLoopState,
        evaluate_repair_decision,
        project_repair_decision_event,
        repair_max_attempts,
    )

    max_attempts = repair_max_attempts(repair_policy)
    decision = evaluate_repair_decision(
        config=CodingRepairLoopConfig(enabled=True, maxAttempts=max_attempts),
        state=CodingRepairLoopState(attemptCount=attempt_count),
        latest_test_evidence=latest_test_evidence,
        is_coding_turn=is_coding_turn,
    )
    return project_repair_decision_event(decision)


def _latest_coding_test_evidence(
    evidence_records: Sequence[object],
) -> Mapping[str, object] | None:
    latest: Mapping[str, object] | None = None
    latest_key: tuple[float, int] | None = None
    for index, record in enumerate(evidence_records):
        evidence = _evidence_mapping(record)
        if evidence is None or not _is_coding_test_evidence(evidence):
            continue
        key = (_evidence_observed_at(evidence), index)
        if latest_key is None or key > latest_key:
            latest = evidence
            latest_key = key
    return latest


def _evidence_mapping(record: object) -> Mapping[str, object] | None:
    if isinstance(record, Mapping):
        return record
    model_dump = getattr(record, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True, mode="python", warnings=False)
        except TypeError:
            dumped = model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _is_coding_test_evidence(evidence: Mapping[str, object]) -> bool:
    haystack = " ".join(
        _string_values(
            evidence,
            (
                "type",
                "evidenceType",
                "evidence_type",
                "kind",
                "evidenceRef",
                "evidence_ref",
                "validatorRef",
                "validator_ref",
                "verifierId",
                "verifier_id",
                "id",
            ),
        )
    ).lower()
    return any(
        marker in haystack
        for marker in (
            "testrun",
            "test_run",
            "test-run",
            "test evidence",
            "test-evidence",
            "dev-coding:test-evidence",
        )
    )


def _string_values(source: Mapping[str, object], keys: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _evidence_observed_at(evidence: Mapping[str, object]) -> float:
    raw = evidence.get("observedAt", evidence.get("observed_at", 0.0))
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _coding_repair_loop_enabled() -> bool:
    from magi_agent.coding.repair_loop import coding_repair_loop_enabled

    return coding_repair_loop_enabled()


def _document_coverage_blocks(mode: str, failed_count: int) -> bool:
    """Whether failed document coverage should flip the pre-final decision.

    14-PR3 (C11): the gate is 3-state (``off`` | ``advisory`` | ``block``). Only
    ``block`` mode lets a failed-coverage count contribute to a ``"block"``
    decision; ``advisory`` records the count for telemetry but never blocks, and
    ``off`` is inert.
    """
    return mode == "block" and failed_count > 0


def _is_research_recipe_scope(
    assembly: RunnerPolicyAssembly,
    live_selected_pack_ids: Sequence[str] = (),
) -> bool:
    """Return whether the assembled policy opted into the research evidence
    contract, so the WS6 soft research-governance notice is in scope.

    Design: WS6 PR6a. Scope is keyed on the recipe having a research evidence
    contract (a research validator label) or a research pack selection, NOT on a
    specific missing label.
    """
    # These research-scope constants stay on the driver (one is also read by a
    # driver-resident method). Import them lazily so this gate module never
    # statically depends on the driver, which re-imports us; behavior unchanged.
    from magi_agent.engine.driver import (  # noqa: PLC0415
        _RESEARCH_CONTRACT_VALIDATOR_LABELS,
        _RESEARCH_RECIPE_PACK_IDS,
    )

    required = set(getattr(assembly, "required_validators", ()) or ())
    if required & _RESEARCH_CONTRACT_VALIDATOR_LABELS:
        return True
    selected = set(getattr(assembly, "selected_pack_ids", ()) or ())
    selected.update(live_selected_pack_ids or ())
    return bool(selected & _RESEARCH_RECIPE_PACK_IDS)


def _resolve_document_coverage_mode_with_preset() -> str:
    """Resolve the document-coverage gate mode, honoring the Customize opt-in seam.

    The base mode comes from ``MAGI_DOCUMENT_AUTHORING_COVERAGE``
    (``off``|``advisory``|``block``). An enabled ``document-authoring-coverage``
    Customize preset promotes an otherwise-``off`` gate to ``block`` for the
    runtime — the same opt-in pattern (env OR preset) as the other satisfier
    seams. Byte-identical when the preset is unset/disabled: the env-resolved
    mode is returned unchanged.
    """
    from magi_agent.config.env import (  # noqa: PLC0415
        resolve_document_authoring_coverage_mode,
    )

    mode = resolve_document_authoring_coverage_mode()
    if mode == "off":
        from magi_agent.customize.runtime_gate import preset_enabled  # noqa: PLC0415

        if preset_enabled("document-authoring-coverage", default=False):
            return "block"
    return mode


def _run_shacl_rules_for_turn(
    policy: object,
    evidence_records: "Sequence[object]",
    *,
    enabled: bool,
    observed_at: int,
) -> tuple[object, ...]:
    """Run enabled SHACL rules against turn evidence and return constraint-check records.

    Pure module-level helper for testability (mirrors the document-coverage
    pattern). Returns a tuple of ``EvidenceRecord`` objects — one per enabled
    ``shacl_constraint`` rule in ``policy``.

    Returns ``()`` when:
    - ``enabled`` is ``False`` (flag OFF → byte-identical to before),
    - ``policy`` is ``None`` (no policy set / MAGI_CUSTOMIZE_VERIFICATION_ENABLED OFF),
    - ``policy`` has no enabled shacl rules.

    Never raises: any per-rule exception is caught and skipped so a bad rule
    cannot break a turn. The ``run_shacl_rule`` producer is itself fail-safe
    (returns ``status="unknown"`` on any internal error), so the belt-and-suspenders
    guard here only catches unexpected attribute / type errors on policy access.
    """
    if not enabled:
        return ()
    if policy is None:
        return ()
    try:
        rules = policy.enabled_shacl_rules()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return ()
    if not rules:
        return ()
    from magi_agent.evidence.shacl_verifier import run_shacl_rule  # noqa: PLC0415

    results: list[object] = []
    for rule in rules:
        try:
            shape_ttl = rule.get("shapeTtl") if isinstance(rule, dict) else None
            rule_id = rule.get("ruleId") if isinstance(rule, dict) else None
            if not shape_ttl or not rule_id:
                continue
            record = run_shacl_rule(
                evidence_records,
                shape_ttl,
                rule_id,
                observed_at=observed_at,
            )
            results.append(record)
        except Exception:  # noqa: BLE001
            continue
    return tuple(results)


def _load_shacl_policy_if_enabled() -> tuple[bool, object]:
    """Resolve the SHACL gate state and load the customize policy when enabled.

    Returns ``(shacl_enabled, policy)`` where:
    - ``shacl_enabled`` is ``True`` only when **both** flags are ON:
      ``MAGI_SHACL_VERIFIER_ENABLED`` (``flag_bool``) AND
      ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` (``flag_profile_bool``).
    - ``policy`` is a ``CustomizeVerificationPolicy`` loaded from the store
      when ``shacl_enabled`` is ``True``, otherwise ``None``.

    Mirrors the precedent in ``magi_agent/customize/apply.py``
    (``apply_verification_overrides``) and ``magi_agent/customize/runtime_gate.py``
    (``preset_enabled``): **both** gate on ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED``
    before reading the store.

    Never raises: any exception → returns ``(False, None)`` (fail-safe).
    """
    try:
        from magi_agent.config.flags import flag_bool as _flag_bool  # noqa: PLC0415
        from magi_agent.config.flags import flag_profile_bool as _flag_profile_bool  # noqa: PLC0415

        shacl_enabled: bool = _flag_bool("MAGI_SHACL_VERIFIER_ENABLED") and _flag_profile_bool(
            "MAGI_CUSTOMIZE_VERIFICATION_ENABLED"
        )
        if shacl_enabled:
            from magi_agent.customize.store import load_overrides as _load_overrides  # noqa: PLC0415
            from magi_agent.customize.verification_policy import (  # noqa: PLC0415
                CustomizeVerificationPolicy as _CVP,
            )

            return True, _CVP.from_overrides(_load_overrides())
        return False, None
    except Exception:  # noqa: BLE001
        return False, None


def _coding_repair_max_attempts(repair_policy: Mapping[str, object]) -> int:
    from magi_agent.coding.repair_loop import repair_max_attempts

    return repair_max_attempts(repair_policy)


def _build_repair_continuation_message(
    *,
    missing_evidence: Sequence[str],
    missing_validators: Sequence[str],
    attempt: int,
    max_attempts: int,
) -> str:
    from magi_agent.coding.repair_loop import build_repair_continuation_message

    return build_repair_continuation_message(
        missing_evidence=tuple(missing_evidence),
        missing_validators=tuple(missing_validators),
        attempt=attempt,
        max_attempts=max_attempts,
    )


def _build_pre_final_verifier_bus_payload(
    *,
    decision: str,
    missing_evidence: list[str],
    missing_validators: list[str],
) -> dict[str, object]:
    """Project the live pre-final gate into public verifier-bus metadata."""

    from magi_agent.harness.verifier_bus import VerifierResultMetadata

    results: list[dict[str, object]] = []
    if missing_evidence:
        results.append(
            VerifierResultMetadata(
                verifierId="tool-evidence-contract",
                status="missing",
                publicSummary="missing required deterministic evidence",
                retryMessage="collect required evidence before final answer",
            ).model_dump(by_alias=True, mode="json", warnings=False)
        )
    if missing_validators:
        results.append(
            VerifierResultMetadata(
                verifierId="dev-coding-verification-audit",
                status="missing",
                publicSummary="missing required validator evidence",
                retryMessage="run required validation before final answer",
            ).model_dump(by_alias=True, mode="json", warnings=False)
        )
    if not results:
        results.append(
            VerifierResultMetadata(
                verifierId="pre-final-evidence-gate",
                status="pass" if decision == "pass" else "audit",
                publicSummary="pre-final evidence gate passed",
            ).model_dump(by_alias=True, mode="json", warnings=False)
        )
    return {
        "metadataOnly": True,
        "decision": decision,
        "results": results,
        "trafficAttached": False,
        "executionAttached": False,
        "failedDocumentCoverage": 0,
    }


def _extract_task_types(harness_state: object | None) -> tuple[str, ...]:
    if not isinstance(harness_state, Mapping):
        return ()
    profile = harness_state.get("taskProfile") or harness_state.get("task_profile")
    if not isinstance(profile, Mapping):
        return ()
    direct = profile.get("taskType") or profile.get("task_type")
    multi = profile.get("taskTypes") or profile.get("task_types")
    values: list[str] = []
    if isinstance(direct, str):
        values.append(direct)
    if isinstance(multi, str):
        values.append(multi)
    elif isinstance(multi, list | tuple):
        values.extend(item for item in multi if isinstance(item, str))
    return tuple(values)


def _normalize_task_type(value: str) -> str:
    return value.strip().lower().replace("_", "-")
