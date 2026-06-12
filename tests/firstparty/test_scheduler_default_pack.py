"""C3.2 — bundled scheduler_default pack + pack-loaded default resolution.

§1 proofs for the schedule policy: the first-party 'which job / when' policy
ships as a removable ``schedule_policy`` pack loaded through the IDENTICAL
loader path a ``~/.magi/packs`` pack uses; a user pack can OVERRIDE the ref;
disabling the pack via the shipped ``config.toml [packs] disable`` convention
removes it from the registry while the kernel fail-open keeps ticking.
"""
from __future__ import annotations

from pathlib import Path


def test_bundled_schedule_policy_resolves_to_cron() -> None:
    from magi_agent.harness.scheduler_executor import (
        CronSchedulePolicy,
        resolve_schedule_policy,
    )

    assert isinstance(resolve_schedule_policy(), CronSchedulePolicy)


def test_user_pack_overrides_bundled_schedule_policy(
    tmp_path: Path, monkeypatch
) -> None:
    """§1 override: a user pack re-declaring schedule_policy:cron@1 replaces the
    bundled first-party policy through the identical loader path."""
    pack = tmp_path / "my-schedule"
    pack.mkdir()
    (pack / "pack.toml").write_text(
        'packId = "user.my-schedule"\ndisplayName = "mine"\n\n'
        "[[provides]]\n"
        'type = "schedule_policy"\n'
        'ref = "schedule_policy:cron@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_cron_override"\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    from magi_agent.harness.scheduler_executor import resolve_schedule_policy
    from magi_agent.packs.discovery import default_search_bases

    bases = list(default_search_bases()) + [tmp_path]
    policy = resolve_schedule_policy(bases=bases)
    assert type(policy).__name__ == "_CronOverridePolicy"


def test_disabling_scheduler_pack_falls_back_fail_open(
    tmp_path: Path, monkeypatch
) -> None:
    """§1 REMOVE: config.toml [packs] disable drops the bundled pack through the
    shipped removal convention (the ref no longer resolves from the registry);
    resolve_schedule_policy then fail-opens to the in-module first-party policy
    so removal never breaks the tick mechanism."""
    from magi_agent.harness.scheduler_executor import (
        CronSchedulePolicy,
        resolve_schedule_policy,
    )
    from magi_agent.packs.discovery import default_search_bases
    from magi_agent.packs.registries import load_into_registries

    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["open' 'magi.scheduler-default"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _report = load_into_registries(list(default_search_bases()))
    assert registries.schedule_policies.resolve("schedule_policy:cron@1") is None

    assert isinstance(resolve_schedule_policy(), CronSchedulePolicy)
