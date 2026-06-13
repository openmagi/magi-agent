"""Group B.1 — bundled first-party ``evidence_firstparty_activity`` producer pack
declares the ToolCall/SkillLoad/SubagentSpawn vocabulary and is discoverable from
the default bundled search base with no config.
"""

from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec
from magi_agent.packs.manifest import load_manifest_from_toml

_PACK_DIR = (
    Path(magi_agent.__file__).resolve().parent
    / "firstparty"
    / "packs"
    / "evidence_firstparty_activity"
)


def test_manifest_declares_three_producer_refs() -> None:
    manifest = load_manifest_from_toml(_PACK_DIR / "pack.toml")
    assert manifest.pack_id == "openmagi.evidence-firstparty-activity"
    entries = [e for e in manifest.provides if e.type == "evidence_producer"]
    assert [e.ref for e in entries] == [
        "evidence:toolCall@1",
        "evidence:skillLoad@1",
        "evidence:subagentSpawn@1",
    ]
    assert all(e.impl and ":" in e.impl for e in entries)


def test_impls_register_matching_specs() -> None:
    from magi_agent.firstparty.packs.evidence_firstparty_activity import impl

    registered: dict[str, ProducerSpec] = {}
    context = EvidenceProducerProvideContext(
        register=lambda ref, spec: registered.__setitem__(ref, spec)
    )
    impl.provide_tool_call_producer(context)
    impl.provide_skill_load_producer(context)
    impl.provide_subagent_spawn_producer(context)
    assert registered["evidence:toolCall@1"].evidence_type == "ToolCall"
    assert registered["evidence:skillLoad@1"].evidence_type == "SkillLoad"
    assert registered["evidence:subagentSpawn@1"].evidence_type == "SubagentSpawn"
    assert all(
        spec.producer_surfaces == ("tool_dispatch",) and spec.public_ref == ref
        for ref, spec in registered.items()
    )


def test_refs_match_activity_module_constants() -> None:
    from magi_agent.evidence.first_party_activity import (
        SKILL_LOAD_REF,
        SUBAGENT_SPAWN_REF,
        TOOL_CALL_REF,
    )

    manifest = load_manifest_from_toml(_PACK_DIR / "pack.toml")
    refs = {e.ref for e in manifest.provides if e.type == "evidence_producer"}
    assert refs == {TOOL_CALL_REF, SKILL_LOAD_REF, SUBAGENT_SPAWN_REF}


def test_load_into_registries_registers_all_three_producers() -> None:
    from magi_agent.packs.registries import load_into_registries

    registries, report = load_into_registries([_PACK_DIR])
    assert "evidence:toolCall@1" in report.registered
    assert "evidence:skillLoad@1" in report.registered
    assert "evidence:subagentSpawn@1" in report.registered
    for ref in ("evidence:toolCall@1", "evidence:skillLoad@1", "evidence:subagentSpawn@1"):
        spec = registries.evidence_producers.resolve(ref)
        assert spec is not None
        assert spec.producer_surfaces == ("tool_dispatch",)
        assert spec.public_ref == ref


def test_discovered_from_bundled_base_and_gate_sees_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    from magi_agent.evidence.first_party_gate import enabled_first_party_activity_refs

    refs = enabled_first_party_activity_refs()
    assert "evidence:toolCall@1" in refs
    assert "evidence:skillLoad@1" in refs
    assert "evidence:subagentSpawn@1" in refs


def test_packs_disable_drops_refs(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[packs]\ndisable = ["openmagi.evidence-firstparty-activity"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGI_CONFIG", str(config))
    from magi_agent.evidence.first_party_gate import enabled_first_party_activity_refs

    refs = enabled_first_party_activity_refs()
    assert "evidence:toolCall@1" not in refs
