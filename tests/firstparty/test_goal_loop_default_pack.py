"""C2.2 — bundled goal_loop_default pack + pack-loaded default resolution.

§1 proofs for the loop policy: the first-party continue/stop policy ships as a
removable ``loop_policy`` pack loaded through the IDENTICAL loader path a
``~/.magi/packs`` pack uses; a user pack can OVERRIDE the ref; disabling the
pack via the shipped ``config.toml [packs] disable`` convention removes it from
the registry while the kernel fail-open keeps the loop alive.
"""
from __future__ import annotations

from pathlib import Path


def test_bundled_loop_policy_is_decide_loop_continuation() -> None:
    from magi_agent.harness.goal_loop_control import (
        decide_loop_continuation,
        resolve_loop_policy,
    )

    assert resolve_loop_policy() is decide_loop_continuation


def test_user_pack_overrides_bundled_loop_policy(tmp_path: Path, monkeypatch) -> None:
    """§1 override: a user pack re-declaring loop_policy:ralph@1 replaces the
    bundled first-party policy through the identical loader path."""
    pack = tmp_path / "my-loop"
    pack.mkdir()
    (pack / "pack.toml").write_text(
        'packId = "user.my-loop"\ndisplayName = "mine"\n\n'
        "[[provides]]\n"
        'type = "loop_policy"\n'
        'ref = "loop_policy:ralph@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_ralph_override"\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    from magi_agent.harness.goal_loop_control import resolve_loop_policy
    from magi_agent.packs.discovery import default_search_bases

    bases = list(default_search_bases()) + [tmp_path]
    policy = resolve_loop_policy(bases=bases)
    assert getattr(policy, "__name__", "") == "_ralph_override"


def test_disabling_goal_loop_pack_falls_back_fail_open(
    tmp_path: Path, monkeypatch
) -> None:
    """§1 REMOVE: config.toml [packs] disable drops the bundled pack through the
    shipped removal convention (the ref no longer resolves from the registry);
    resolve_loop_policy then fail-opens to the in-module first-party function so
    removal never breaks a live loop."""
    from magi_agent.harness.goal_loop_control import (
        decide_loop_continuation,
        resolve_loop_policy,
    )
    from magi_agent.packs.discovery import default_search_bases
    from magi_agent.packs.registries import load_into_registries

    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["open' 'magi.goal-loop-default"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _report = load_into_registries(list(default_search_bases()))
    assert registries.loop_policies.resolve("loop_policy:ralph@1") is None

    assert resolve_loop_policy() is decide_loop_continuation
