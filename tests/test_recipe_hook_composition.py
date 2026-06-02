from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openmagi_core_agent.recipes.hook_composition import (
    EffectiveRecipeHookContract,
    HookContribution,
    compose_hook_contributions,
)


def _contribution(
    recipe_ref: str,
    hook_id: str,
    *,
    stage: str = "beforeToolUse",
    priority: int = 100,
    scope: tuple[str, ...] = ("all",),
    idempotency_key: str | None = None,
    blocking: bool = False,
    failure_mode: str = "fail_open",
    side_effectful: bool = False,
    security_critical: bool = False,
    private_config: dict[str, object] | None = None,
) -> HookContribution:
    payload: dict[str, object] = {
        "recipeRef": recipe_ref,
        "hookId": hook_id,
        "stage": stage,
        "priority": priority,
        "scope": scope,
        "idempotencyKey": idempotency_key,
        "blocking": blocking,
        "failureMode": failure_mode,
        "sideEffectful": side_effectful,
        "securityCritical": security_critical,
        "privateConfig": private_config or {},
    }
    payload["contributionDigest"] = HookContribution.compute_contribution_digest(payload)
    return HookContribution._from_registry_contribution(payload)


def test_duplicate_observer_with_same_idempotency_key_dedupes() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.audit",
                idempotency_key="hook.audit:turn:sha256:abc",
            ),
            _contribution(
                "recipe.beta",
                "hook.audit",
                idempotency_key="hook.audit:turn:sha256:abc",
            ),
        )
    )

    assert result.blocked is False
    assert result.conflicts == ()
    assert tuple(hook.hook_id for hook in result.hooks) == ("hook.audit",)
    assert result.hooks[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_duplicate_blocking_non_idempotent_hook_conflicts() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.policy",
                blocking=True,
                failure_mode="fail_closed",
            ),
            _contribution(
                "recipe.beta",
                "hook.policy",
                blocking=True,
                failure_mode="fail_closed",
            ),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].blocking is True
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_duplicate_blocking_non_idempotent_hook_conflicts_across_priorities() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.policy",
                priority=10,
                blocking=True,
                failure_mode="fail_closed",
            ),
            _contribution(
                "recipe.beta",
                "hook.policy",
                priority=20,
                blocking=True,
                failure_mode="fail_closed",
            ),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_duplicate_side_effectful_non_idempotent_hook_conflicts() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.writer",
                side_effectful=True,
            ),
            _contribution(
                "recipe.beta",
                "hook.writer",
                side_effectful=True,
            ),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_duplicate_side_effectful_non_idempotent_hook_conflicts_across_priorities() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.writer",
                priority=10,
                side_effectful=True,
            ),
            _contribution(
                "recipe.beta",
                "hook.writer",
                priority=20,
                side_effectful=True,
            ),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_duplicate_neutral_non_idempotent_hook_conflicts() -> None:
    result = compose_hook_contributions(
        (
            _contribution("recipe.alpha", "hook.observe"),
            _contribution("recipe.beta", "hook.observe"),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].blocking is True
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_mixed_idempotent_and_non_idempotent_duplicate_hook_conflicts() -> None:
    result = compose_hook_contributions(
        (
            _contribution("recipe.alpha", "hook.observe"),
            _contribution(
                "recipe.beta",
                "hook.observe",
                idempotency_key="hook.observe:turn:sha256:abc",
            ),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].blocking is True
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_security_critical_hook_survives_opt_out() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.safety",
                "hook.safety_gate",
                blocking=True,
                failure_mode="fail_closed",
                security_critical=True,
            ),
            _contribution("recipe.observer", "hook.telemetry"),
        ),
        disabled_hook_ids=("hook.safety_gate", "hook.telemetry"),
    )

    assert result.blocked is False
    assert tuple(hook.hook_id for hook in result.hooks) == ("hook.safety_gate",)
    assert result.hooks[0].security_critical is True
    assert result.conflicts[0].code == "security_critical_hook_opt_out_rejected"
    assert result.conflicts[0].blocking is False


def test_hook_ordering_stable_by_stage_priority_ref() -> None:
    contributions = (
        _contribution("recipe.zulu", "hook.after", stage="afterToolUse", priority=1),
        _contribution("recipe.beta", "hook.middle", stage="beforeToolUse", priority=20),
        _contribution("recipe.alpha", "hook.first", stage="beforeToolUse", priority=20),
        _contribution("recipe.alpha", "hook.early", stage="beforeToolUse", priority=5),
    )

    first = compose_hook_contributions(contributions)
    second = compose_hook_contributions(tuple(reversed(contributions)))

    assert tuple(
        (hook.stage, hook.priority, hook.recipe_refs, hook.hook_id)
        for hook in first.hooks
    ) == (
        ("beforeToolUse", 5, ("recipe.alpha",), "hook.early"),
        ("beforeToolUse", 20, ("recipe.alpha",), "hook.first"),
        ("beforeToolUse", 20, ("recipe.beta",), "hook.middle"),
        ("afterToolUse", 1, ("recipe.zulu",), "hook.after"),
    )
    assert first.public_projection() == second.public_projection()
    assert first.composition_digest == second.composition_digest


def test_hook_ordering_includes_scope_and_idempotency_key() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.team",
                priority=10,
                scope=("team",),
                idempotency_key="hook.order:b",
            ),
            _contribution(
                "recipe.alpha",
                "hook.agent_z",
                priority=10,
                scope=("agent",),
                idempotency_key="hook.order:z",
            ),
            _contribution(
                "recipe.alpha",
                "hook.agent_a",
                priority=10,
                scope=("agent",),
                idempotency_key="hook.order:a",
            ),
        )
    )

    assert tuple((hook.scope, hook.idempotency_key, hook.hook_id) for hook in result.hooks) == (
        (("agent",), "hook.order:a", "hook.agent_a"),
        (("agent",), "hook.order:z", "hook.agent_z"),
        (("team",), "hook.order:b", "hook.team"),
    )


def test_non_idempotent_observer_duplicate_conflicts_across_priorities() -> None:
    result = compose_hook_contributions(
        (
            _contribution("recipe.alpha", "hook.observe", priority=20),
            _contribution("recipe.beta", "hook.observe", priority=10),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "non_idempotent_hook_duplicate"
    assert result.conflicts[0].blocking is True
    assert result.conflicts[0].recipe_refs == ("recipe.alpha", "recipe.beta")


def test_same_idempotency_key_does_not_dedupe_across_stages() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.audit_before",
                stage="beforeToolUse",
                idempotency_key="hook.audit:turn:sha256:abc",
            ),
            _contribution(
                "recipe.beta",
                "hook.audit_after",
                stage="afterToolUse",
                idempotency_key="hook.audit:turn:sha256:abc",
            ),
        )
    )

    assert result.blocked is False
    assert tuple((hook.stage, hook.hook_id) for hook in result.hooks) == (
        ("beforeToolUse", "hook.audit_before"),
        ("afterToolUse", "hook.audit_after"),
    )


def test_same_idempotency_key_with_different_hook_ids_conflicts() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.observer",
                "hook.audit",
                idempotency_key="hook.shared:key",
            ),
            _contribution(
                "recipe.safety",
                "hook.safety_gate",
                idempotency_key="hook.shared:key",
                blocking=True,
                failure_mode="fail_closed",
                security_critical=True,
            ),
        )
    )

    assert result.blocked is True
    assert result.hooks == ()
    assert result.conflicts[0].code == "idempotency_key_hook_collision"
    assert result.conflicts[0].hook_ids == ("hook.audit", "hook.safety_gate")
    assert result.conflicts[0].recipe_refs == ("recipe.observer", "recipe.safety")


def test_same_idempotency_key_dedupes_across_priorities() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.audit",
                priority=20,
                idempotency_key="hook.shared:key",
            ),
            _contribution(
                "recipe.beta",
                "hook.audit",
                priority=10,
                idempotency_key="hook.shared:key",
            ),
        )
    )

    assert result.blocked is False
    assert result.conflicts == ()
    assert tuple((hook.priority, hook.hook_id, hook.recipe_refs) for hook in result.hooks) == (
        (10, "hook.audit", ("recipe.alpha", "recipe.beta")),
    )


def test_public_projection_redacts_hook_private_config() -> None:
    result = compose_hook_contributions(
        (
            _contribution(
                "recipe.alpha",
                "hook.redacted",
                idempotency_key="hook.redacted:private",
                private_config={
                    "token": "sk-proj-private-value",
                    "path": "/Users/kevin/private/config.json",
                },
            ),
        )
    )

    projection = result.public_projection()
    dumped = result.model_dump(by_alias=True, mode="json")
    dumped_json = result.model_dump_json(by_alias=True)
    serialized = json.dumps(projection, sort_keys=True) + json.dumps(dumped) + dumped_json

    assert projection["hooks"][0]["privateConfigRedacted"] is True
    assert "sk-proj-private-value" not in serialized
    assert "/Users/kevin/private/config.json" not in serialized
    assert "privateConfigDigest" in serialized


def test_hook_contract_projection_rejects_mutated_digest_mismatch() -> None:
    result = compose_hook_contributions((_contribution("recipe.alpha", "hook.audit"),))
    result.__dict__["hooks"] = ()

    try:
        result.public_projection()
    except ValueError as exc:
        assert "hook composition digest mismatch" in str(exc)
    else:
        raise AssertionError("expected hook composition digest mismatch")


def test_hook_contract_rejects_live_authority_flags() -> None:
    result = compose_hook_contributions((_contribution("recipe.alpha", "hook.audit"),))

    with pytest.raises(ValidationError):
        EffectiveRecipeHookContract.model_validate(
            {
                "schemaVersion": result.schema_version,
                "hooks": result.hooks,
                "conflicts": result.conflicts,
                "blocked": result.blocked,
                "defaultOff": True,
                "trafficAttached": True,
                "executionAttached": False,
                "liveActivation": False,
                "compositionDigest": result.composition_digest,
            }
        )

    with pytest.raises(
        ValueError,
        match="hook composition contract authority fields are immutable",
    ):
        result.model_copy(update={"liveActivation": True})


def test_hook_contract_model_construct_canonicalizes_live_authority_flags() -> None:
    result = compose_hook_contributions((_contribution("recipe.alpha", "hook.audit"),))

    constructed = EffectiveRecipeHookContract.model_construct(
        schema_version=result.schema_version,
        hooks=result.hooks,
        conflicts=result.conflicts,
        blocked=result.blocked,
        default_off=False,
        traffic_attached=True,
        execution_attached=True,
        live_activation=True,
        composition_digest=result.composition_digest,
    )

    assert constructed.default_off is True
    assert constructed.traffic_attached is False
    assert constructed.execution_attached is False
    assert constructed.live_activation is False
    assert constructed.public_projection()["trafficAttached"] is False


def test_hook_contract_model_construct_rejects_digest_mismatch() -> None:
    result = compose_hook_contributions((_contribution("recipe.alpha", "hook.audit"),))

    with pytest.raises(ValidationError, match="hook composition digest mismatch"):
        EffectiveRecipeHookContract.model_construct(
            schema_version=result.schema_version,
            hooks=result.hooks,
            conflicts=result.conflicts,
            blocked=result.blocked,
            default_off=True,
            traffic_attached=False,
            execution_attached=False,
            live_activation=False,
            composition_digest="sha256:" + "1" * 64,
        )
