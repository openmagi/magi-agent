"""Built-in (first-party) policy opt-out: store persistence + env projection.

First-party policies (``verify_before_replying``, ``source_citation``) fire on
``MAGI_*_ENABLED`` env flags, never on policy scope, so the Customize surface
could display them but not turn them off. This closes that gap by mirroring the
``control_plane_overrides`` seam: a curated catalog of *user-disableable*
builtins projected onto their master env flag as an explicit overwrite.

``source_citation`` is intentionally NOT in the catalog (floor: its gate can
BLOCK in ``repair`` mode), so a user cannot walk it back through this seam.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.customize.builtin_policy_overrides import (
    BUILTIN_POLICY_TOGGLES,
    apply_builtin_policy_overrides_to_env,
    builtin_policy_toggle_catalog,
)
from magi_agent.customize.store import (
    DEFAULT_OVERRIDES,
    load_overrides,
    set_builtin_policy_override,
)

VERIFY_ENV = "MAGI_VERIFY_BEFORE_REPLYING_ENABLED"
CITATION_ENV = "MAGI_SOURCE_CITATION_ENABLED"


# --------------------------------------------------------------------------- #
# Catalog                                                                      #
# --------------------------------------------------------------------------- #
def test_verify_before_replying_is_in_catalog_mapped_to_its_flag() -> None:
    by_id = {t.id: t for t in BUILTIN_POLICY_TOGGLES}
    assert "verify_before_replying" in by_id
    assert by_id["verify_before_replying"].env_var == VERIFY_ENV


def test_source_citation_is_a_floor_excluded_from_catalog() -> None:
    # source_citation's gate can BLOCK (repair mode) -> not user-disableable.
    ids = {t.id for t in BUILTIN_POLICY_TOGGLES}
    env_vars = {t.env_var for t in BUILTIN_POLICY_TOGGLES}
    assert "source_citation" not in ids
    assert CITATION_ENV not in env_vars


def test_catalog_excludes_hard_safety_flags() -> None:
    env_vars = {t.env_var for t in BUILTIN_POLICY_TOGGLES}
    for forbidden in (
        "MAGI_EGRESS_GATE_ENABLED",
        "MAGI_GATE5B_GOVERNANCE_ENABLED",
        "MAGI_SERVE_EVIDENCE_ENABLED",
        "MAGI_KERNEL_RECIPE_PACKS_ENABLED",
    ):
        assert forbidden not in env_vars


def test_catalog_is_serializable_with_label_and_description() -> None:
    cat = builtin_policy_toggle_catalog({})
    entry = next(e for e in cat if e["id"] == "verify_before_replying")
    assert entry["env_var"] == VERIFY_ENV
    assert entry["label"] and entry["description"]


def test_catalog_enabled_reflects_effective_default_on() -> None:
    # verify is default-ON via a profile_bool: with the flag UNSET it must still
    # report enabled (a raw is_true(unset) would wrongly report "off").
    entry = next(
        e for e in builtin_policy_toggle_catalog({}) if e["id"] == "verify_before_replying"
    )
    assert entry["enabled"] is True


def test_catalog_enabled_reflects_explicit_off() -> None:
    entry = next(
        e
        for e in builtin_policy_toggle_catalog({VERIFY_ENV: "0"})
        if e["id"] == "verify_before_replying"
    )
    assert entry["enabled"] is False


# --------------------------------------------------------------------------- #
# Env projection — overwrite precedence (both directions)                      #
# --------------------------------------------------------------------------- #
def test_explicit_false_disables_the_default_on_policy() -> None:
    env: dict[str, str] = {}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": {"verify_before_replying": False}}
    )
    assert env[VERIFY_ENV] == "0"


def test_explicit_true_re_enables_a_disabled_policy() -> None:
    # The clean re-enable a setdefault applier could not do.
    env = {VERIFY_ENV: "0"}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": {"verify_before_replying": True}}
    )
    assert env[VERIFY_ENV] == "1"


def test_absent_policy_leaves_env_untouched() -> None:
    env = {VERIFY_ENV: "1"}
    apply_builtin_policy_overrides_to_env(env, {"builtin_policies": {}})
    assert env[VERIFY_ENV] == "1"


def test_empty_overrides_is_a_noop() -> None:
    env = {VERIFY_ENV: "1"}
    apply_builtin_policy_overrides_to_env(env, None)
    apply_builtin_policy_overrides_to_env(env, {})
    assert env[VERIFY_ENV] == "1"


def test_floor_id_never_touches_its_env() -> None:
    # A hand-edited disable of the floor policy must NOT project.
    env = {CITATION_ENV: "1", VERIFY_ENV: "1"}
    apply_builtin_policy_overrides_to_env(
        env,
        {"builtin_policies": {"source_citation": False, "made-up": False}},
    )
    assert env[CITATION_ENV] == "1"
    assert env[VERIFY_ENV] == "1"


def test_flag_shaped_id_never_leaks_through() -> None:
    env = {"MAGI_EGRESS_GATE_ENABLED": "1"}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": {"MAGI_EGRESS_GATE_ENABLED": False}}
    )
    assert env["MAGI_EGRESS_GATE_ENABLED"] == "1"


def test_non_bool_value_is_ignored() -> None:
    env = {VERIFY_ENV: "1"}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": {"verify_before_replying": "0"}}
    )
    assert env[VERIFY_ENV] == "1"


def test_malformed_overrides_fail_soft() -> None:
    env = {VERIFY_ENV: "1"}
    apply_builtin_policy_overrides_to_env(
        env, {"builtin_policies": ["verify_before_replying"]}
    )
    assert env[VERIFY_ENV] == "1"


# --------------------------------------------------------------------------- #
# Store                                                                         #
# --------------------------------------------------------------------------- #
def test_default_overrides_has_empty_builtin_policies_section() -> None:
    assert DEFAULT_OVERRIDES["builtin_policies"] == {}


def test_set_builtin_policy_override_roundtrips_and_retains_false(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    out = set_builtin_policy_override("verify_before_replying", False, path=p)
    assert out["builtin_policies"]["verify_before_replying"] is False
    assert load_overrides(p)["builtin_policies"]["verify_before_replying"] is False


def test_normalize_drops_non_bool_builtin_policy_entries(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    p.write_text(
        '{"builtin_policies": {"verify_before_replying": false, "x": "yes", "y": 1}}',
        encoding="utf-8",
    )
    section = load_overrides(p)["builtin_policies"]
    assert section == {"verify_before_replying": False}


def test_store_to_env_end_to_end(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    set_builtin_policy_override("verify_before_replying", False, path=p)
    overrides = load_overrides(p)
    env: dict[str, str] = {}
    apply_builtin_policy_overrides_to_env(env, overrides)
    assert env[VERIFY_ENV] == "0"


# --------------------------------------------------------------------------- #
# Cross-consistency with the Policy model (O3 floor)                           #
# --------------------------------------------------------------------------- #
def test_every_toggle_is_a_real_user_disableable_builtin_policy() -> None:
    from magi_agent.customize.policies import get_policy

    for toggle in BUILTIN_POLICY_TOGGLES:
        policy = get_policy(toggle.id)
        assert policy is not None, f"toggle {toggle.id} has no builtin policy"
        assert policy.origin == "builtin"
        assert policy.user_disableable is True


def test_verify_policy_is_user_disableable_source_citation_is_not() -> None:
    from magi_agent.customize.policies import get_policy

    assert get_policy("verify_before_replying").user_disableable is True
    assert get_policy("source_citation").user_disableable is False


def test_no_non_disableable_builtin_is_ever_a_toggle() -> None:
    # Reverse invariant (guards a future blocking floor added to the catalog by
    # mistake): every builtin marked user_disableable=False MUST be absent from
    # the opt-out catalog, so it can never be turned off through this seam.
    from magi_agent.customize.policies import BUILTIN_POLICIES

    toggle_ids = {t.id for t in BUILTIN_POLICY_TOGGLES}
    for policy in BUILTIN_POLICIES:
        if not policy.user_disableable:
            assert policy.policy_id not in toggle_ids, (
                f"floor policy {policy.policy_id!r} must not be a user toggle"
            )


def test_user_disableable_surfaces_in_policy_payload() -> None:
    from magi_agent.customize.policies import get_policy

    payload = get_policy("source_citation").to_payload()
    assert payload["userDisableable"] is False
