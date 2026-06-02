from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from magi_agent.harness.presets import builtin_preset_keys


DEFAULT_PROFILE_NAME = "openmagi-opinionated"

#: Opt-in feature-pack that promotes child execution from the local-fake
#: placeholder surface to the real (local) ADK turn-runner surface.  It is
#: default-OFF and NOT opt-out: real child execution can only be reached when a
#: caller explicitly opts into this pack AND the workflow-executor env gate is
#: on.  See ``runtime/child_runner_boundary.py`` for the gated promotion.
REAL_CHILD_EXECUTION_PACK_NAME = "real-child-execution"


class FeaturePack(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Literal[
        "coding",
        "research",
        "verification",
        "local-tools",
        "cloud",
        "real-child-execution",
    ]
    enabled_by_default: bool
    opt_out: bool
    hard_safety: bool = False


class HardSafetyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled_by_default: bool = True
    opt_out: bool = False
    gates: tuple[str, ...] = (
        "permission-arbiter",
        "path-safety",
        "secret-safety",
        "sealed-file-policy",
        "git-safety",
    )


class RuntimeProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Literal["openmagi-opinionated"] = DEFAULT_PROFILE_NAME
    hard_safety: HardSafetyPolicy
    harness_packs: tuple[FeaturePack, ...]
    builtin_preset_keys: tuple[str, ...] = ()


def build_default_profile() -> RuntimeProfile:
    return RuntimeProfile(
        hard_safety=HardSafetyPolicy(),
        harness_packs=(
            FeaturePack(name="coding", enabled_by_default=True, opt_out=True),
            FeaturePack(name="research", enabled_by_default=True, opt_out=True),
            FeaturePack(name="verification", enabled_by_default=True, opt_out=True),
            FeaturePack(name="local-tools", enabled_by_default=True, opt_out=True),
            FeaturePack(name="cloud", enabled_by_default=True, opt_out=True),
            # Opt-in, default-OFF: the first real child-agent execution surface.
            FeaturePack(
                name=REAL_CHILD_EXECUTION_PACK_NAME,
                enabled_by_default=False,
                opt_out=False,
            ),
        ),
        builtin_preset_keys=builtin_preset_keys(),
    )


def real_child_execution_pack_enabled(
    profile: RuntimeProfile,
    *,
    opted_in_packs: Sequence[str] | None = None,
) -> bool:
    """Return True only when the real-child-execution pack is explicitly opted in.

    The pack is default-OFF and not opt-out, so it is enabled only when the
    caller lists it in *opted_in_packs*.  ``enabled_by_default`` is never
    honoured for this pack (it is always ``False``); this helper exists so the
    only way to flip the runtime into real child execution is an explicit
    opt-in, never an ambient default.
    """
    pack = next(
        (p for p in profile.harness_packs if p.name == REAL_CHILD_EXECUTION_PACK_NAME),
        None,
    )
    if pack is None:
        return False
    if pack.enabled_by_default:
        # Defensive: the pack must never be ambient-on.
        return False
    return REAL_CHILD_EXECUTION_PACK_NAME in tuple(opted_in_packs or ())
