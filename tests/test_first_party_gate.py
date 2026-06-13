from __future__ import annotations

from pathlib import Path

from magi_agent.evidence.first_party_gate import (
    FIRST_PARTY_EVIDENCE_DISABLED_ENV,
    enabled_first_party_activity_refs,
    first_party_evidence_disabled,
)

_PACK_TOML = "\n".join(
    (
        'packId = "user.test-evidence"',
        'displayName = "Test evidence pack"',
        'version = "1.0.0"',
        "",
        "[[provides]]",
        'type = "evidence_producer"',
        'ref = "evidence:toolCall@1"',
        'impl = "user_pack.impl:provide"',
        "",
        "[[provides]]",
        'type = "evidence_producer"',
        'ref = "evidence:skillLoad@1"',
        'impl = "user_pack.impl:provide_skills"',
    )
)


def _write_pack(base: Path) -> None:
    pack_dir = base / "test_evidence"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.toml").write_text(_PACK_TOML, encoding="utf-8")


def test_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv(FIRST_PARTY_EVIDENCE_DISABLED_ENV, raising=False)
    assert first_party_evidence_disabled() is False
    monkeypatch.setenv(FIRST_PARTY_EVIDENCE_DISABLED_ENV, "1")
    assert first_party_evidence_disabled() is True
    monkeypatch.setenv(FIRST_PARTY_EVIDENCE_DISABLED_ENV, "off")
    assert first_party_evidence_disabled() is False
    # injectable env dict — process env not consulted
    assert first_party_evidence_disabled(env={}) is False
    assert first_party_evidence_disabled(env={"MAGI_FP_EVIDENCE_DISABLED": "1"}) is True


def test_refs_from_static_manifests(tmp_path, monkeypatch) -> None:
    _write_pack(tmp_path)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))  # empty config
    refs = enabled_first_party_activity_refs(bases=[tmp_path])
    assert refs == ("evidence:toolCall@1", "evidence:skillLoad@1")


def test_disabled_pack_drops_refs(tmp_path, monkeypatch) -> None:
    _write_pack(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text('[packs]\ndisable = ["user.test-evidence"]\n', encoding="utf-8")
    monkeypatch.setenv("MAGI_CONFIG", str(config))
    assert enabled_first_party_activity_refs(bases=[tmp_path]) == ()


def test_missing_bases_and_bad_manifest_fail_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    assert enabled_first_party_activity_refs(bases=[tmp_path / "absent"]) == ()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "pack.toml").write_text("not = [valid", encoding="utf-8")
    assert enabled_first_party_activity_refs(bases=[broken.parent]) == ()


def test_broken_pack_does_not_poison_healthy_packs(tmp_path, monkeypatch) -> None:
    _write_pack(tmp_path)  # healthy pack with 2 refs
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "pack.toml").write_text("not = [valid", encoding="utf-8")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    refs = enabled_first_party_activity_refs(bases=[tmp_path])
    assert refs == ("evidence:toolCall@1", "evidence:skillLoad@1")


def test_bundled_pack_enabled_by_default(monkeypatch, tmp_path) -> None:
    # default search bases include magi_agent/firstparty/packs — once Task 5
    # lands, the bundled activity pack's refs appear with no config at all.
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "config.toml"))
    refs = enabled_first_party_activity_refs()
    assert "evidence:toolCall@1" in refs
