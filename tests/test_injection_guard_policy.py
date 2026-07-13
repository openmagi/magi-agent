"""U6 RED -- injection_guard builtin policy catalog + opt-out toggle.

injection_guard is a non-blocking, user-disableable first-party policy: it
scans external tool-result content for injection heuristics, records findings,
and (mode ``annotate``) prepends a static advisory header on HIGH severity.
Because it never blocks, it is user-disableable and rides
``apply_builtin_policy_overrides_to_env`` as one ``BuiltinPolicyToggle`` entry.
"""
from __future__ import annotations

from pathlib import Path

INJECTION_ENV = "MAGI_INJECTION_GUARD_ENABLED"


# --------------------------------------------------------------------------- #
# Builtin policy catalog                                                        #
# --------------------------------------------------------------------------- #
def test_injection_guard_is_a_builtin_policy() -> None:
    from magi_agent.customize.policies import get_policy

    policy = get_policy("injection_guard")
    assert policy is not None
    assert policy.origin == "builtin"
    assert policy.user_disableable is True
    assert policy.rule_ids == (
        "injection_guard.scan",
        "injection_guard.annotate",
        "injection_guard.nudge",
    )
    # No PolicyBinding: injection findings are audit records, never unlock
    # evidence (design section 11).
    assert policy.binding is None


def test_injection_guard_listed_in_builtins_sorted() -> None:
    from magi_agent.customize.policies import list_policies

    ids = [p.policy_id for p in list_policies()]
    assert "injection_guard" in ids
    # list_policies is sorted by id.
    assert ids == sorted(ids)


# --------------------------------------------------------------------------- #
# Opt-out toggle catalog                                                        #
# --------------------------------------------------------------------------- #
def test_injection_guard_toggle_is_in_catalog() -> None:
    from magi_agent.customize.builtin_policy_overrides import BUILTIN_POLICY_TOGGLES

    by_id = {t.id: t for t in BUILTIN_POLICY_TOGGLES}
    assert "injection_guard" in by_id
    assert by_id["injection_guard"].env_var == INJECTION_ENV


def test_injection_guard_toggle_reflects_effective_default_on() -> None:
    from magi_agent.customize.builtin_policy_overrides import (
        builtin_policy_toggle_catalog,
    )

    entry = next(
        e for e in builtin_policy_toggle_catalog({}) if e["id"] == "injection_guard"
    )
    # Default-ON via profile_bool with the flag UNSET.
    assert entry["enabled"] is True
    assert entry["label"] and entry["description"]


def test_injection_guard_toggle_reflects_explicit_off() -> None:
    from magi_agent.customize.builtin_policy_overrides import (
        builtin_policy_toggle_catalog,
    )

    entry = next(
        e
        for e in builtin_policy_toggle_catalog({INJECTION_ENV: "0"})
        if e["id"] == "injection_guard"
    )
    assert entry["enabled"] is False


def test_injection_guard_toggle_projects_both_directions() -> None:
    from magi_agent.customize.builtin_policy_overrides import (
        apply_builtin_policy_overrides_to_env,
    )

    env: dict[str, str] = {}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": {"injection_guard": False}}
    )
    assert env[INJECTION_ENV] == "0"

    env = {INJECTION_ENV: "0"}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": {"injection_guard": True}}
    )
    assert env[INJECTION_ENV] == "1"


def test_injection_guard_toggle_is_a_real_user_disableable_builtin() -> None:
    from magi_agent.customize.builtin_policy_overrides import BUILTIN_POLICY_TOGGLES
    from magi_agent.customize.policies import get_policy

    toggle = next(t for t in BUILTIN_POLICY_TOGGLES if t.id == "injection_guard")
    policy = get_policy(toggle.id)
    assert policy is not None
    assert policy.origin == "builtin"
    assert policy.user_disableable is True


def test_injection_guard_store_to_env_end_to_end(tmp_path: Path) -> None:
    from magi_agent.customize.builtin_policy_overrides import (
        apply_builtin_policy_overrides_to_env,
    )
    from magi_agent.customize.store import load_overrides, set_builtin_policy_override

    p = tmp_path / "customize.json"
    set_builtin_policy_override("injection_guard", False, path=p)
    overrides = load_overrides(p)
    env: dict[str, str] = {}
    apply_builtin_policy_overrides_to_env(env, overrides)
    assert env[INJECTION_ENV] == "0"
