"""C4.2 — bundled memory_strategies_default pack + pack-loaded defaults.

§1 proofs for the memory strategies: the first-party compaction-denial /
recall-projection / review-trigger strategies ship as a removable
``memory_strategy`` pack loaded through the IDENTICAL loader path a
``~/.magi/packs`` pack uses; a user pack can OVERRIDE a ref; disabling the
pack via the shipped ``config.toml [packs] disable`` convention removes it
from the registry while the harness fail-open keeps the exact legacy
defaults. The memory STORES and receipt envelopes stay kernel.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


def _compaction_fixtures() -> tuple[object, object]:
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionPolicy,
        MemoryCompactionRequest,
    )

    request = MemoryCompactionRequest.model_validate(
        {
            "providerId": "p",
            "turnId": "t",
            "sourceRefs": ("evidence:s",),
            "evidenceRefs": ("evidence:e",),
        }
    )
    policy = MemoryCompactionPolicy.model_validate(
        {
            "policyRef": "policy:x",
            "policySnapshotRef": "policy:x@snap",
            "localFakeCompactionAllowed": True,
        }
    )
    return request, policy


def test_bundled_memory_strategies_load_and_resolve() -> None:
    from magi_agent.harness.memory_compaction import (
        _compaction_denial_reasons,
        resolve_memory_strategy,
    )
    from magi_agent.harness.memory_review import should_run_review
    from magi_agent.recipes.first_party.memory_recall import (
        MemoryRecallProjectionPolicy,
    )

    assert (
        resolve_memory_strategy("memory_strategy:compaction-denial@1", default=None)
        is _compaction_denial_reasons
    )
    assert (
        resolve_memory_strategy("memory_strategy:review-trigger@1", default=None)
        is should_run_review
    )
    # latest_user_text is a required per-request field, so the projection
    # strategy registers the CLASS — an addressable factory, not an instance.
    assert (
        resolve_memory_strategy("memory_strategy:recall-projection@1", default=None)
        is MemoryRecallProjectionPolicy
    )


def test_user_pack_overrides_bundled_memory_strategy(
    tmp_path: Path, monkeypatch
) -> None:
    """§1 override: a user pack re-declaring memory_strategy:compaction-denial@1
    replaces the bundled first-party strategy through the identical loader path,
    and the harness consumes the override end-to-end."""
    pack = tmp_path / "my-memory"
    pack.mkdir()
    (pack / "pack.toml").write_text(
        'packId = "user.my-memory"\ndisplayName = "mine"\n\n'
        "[[provides]]\n"
        'type = "memory_strategy"\n'
        'ref = "memory_strategy:compaction-denial@1"\n'
        'impl = "tests.packs.pack_c_fixture_impls:provide_compaction_denial_override"\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionHarness,
        resolve_memory_strategy,
    )
    from magi_agent.packs.discovery import default_search_bases

    bases = list(default_search_bases()) + [tmp_path]
    strategy = resolve_memory_strategy(
        "memory_strategy:compaction-denial@1", default=None, bases=bases
    )
    request, policy = _compaction_fixtures()
    assert strategy(request, policy) == ("blocked", ("user_denial_override",))

    harness = MemoryCompactionHarness(
        {"enabled": True, "localFakeAdapterEnabled": True},
        denial_strategy=strategy,
    )
    result = asyncio.run(harness.compact(request=request, policy=policy))
    assert result.status == "blocked"
    assert result.reason_codes == ("user_denial_override",)


def test_disabling_memory_pack_falls_back_fail_open(
    tmp_path: Path, monkeypatch
) -> None:
    """§1 REMOVE: config.toml [packs] disable drops the bundled pack through the
    shipped removal convention (the refs no longer resolve from the registry);
    resolve_memory_strategy then fail-opens to the in-module first-party
    strategies so removal never breaks the memory boundaries."""
    from magi_agent.harness.memory_compaction import (
        MemoryCompactionHarness,
        _compaction_denial_reasons,
        resolve_memory_strategy,
    )
    from magi_agent.harness.memory_review import (
        MemoryReviewConfig,
        MemoryReviewHarness,
        should_run_review,
    )
    from magi_agent.packs.discovery import default_search_bases
    from magi_agent.packs.registries import load_into_registries

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[packs]\ndisable = ["open' 'magi.memory-strategies-default"]\n'
    )
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _report = load_into_registries(list(default_search_bases()))
    for ref in (
        "memory_strategy:compaction-denial@1",
        "memory_strategy:recall-projection@1",
        "memory_strategy:review-trigger@1",
    ):
        assert registries.memory_strategies.resolve(ref) is None

    assert (
        resolve_memory_strategy("memory_strategy:compaction-denial@1", default=None)
        is None
    )

    # The harnesses keep working on the exact legacy defaults (fail-open).
    request, policy = _compaction_fixtures()
    harness = MemoryCompactionHarness({"enabled": True, "localFakeAdapterEnabled": True})
    assert harness._denial_strategy is _compaction_denial_reasons
    result = asyncio.run(harness.compact(request=request, policy=policy))
    assert result.status == "success"

    review = MemoryReviewHarness(MemoryReviewConfig(enabled=True, intervalTurns=10))
    assert review._trigger is should_run_review
    assert review.should_run(turn_count=10) is True
