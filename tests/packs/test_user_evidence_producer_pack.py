"""Group B.2 — a USER evidence_producer pack can ADD / OVERRIDE / REMOVE refs with
no first-party privilege (§1). Override = last-wins load order; remove = ``[packs]
disable`` by pack_id (the real Phase-3 convention)."""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.registries import load_into_registries

_FIRST_PARTY_ROOT = Path(magi_agent.__file__).parent / "firstparty" / "packs"

_IMPL_BODY = (
    "from magi_agent.packs.context import EvidenceProducerProvideContext, ProducerSpec\n"
    "def provide_custom(ctx):\n"
    "    ctx.register('evidence:custom-snap@1', ProducerSpec(\n"
    "        evidence_type='custom:snap', public_ref='evidence:customSnap@1',\n"
    "        producer_surfaces=('tool_host',)))\n"
    "def provide_gitdiff_override(ctx):\n"
    # F-4: first-party pack now registers ``evidence:git-diff@1`` —
    # override targets the canonical ref.
    "    ctx.register('evidence:git-diff@1', ProducerSpec(\n"
    "        evidence_type='GitDiff', public_ref='evidence:gitDiffV2@1',\n"
    "        producer_surfaces=('tool_host','verifier')))\n"
    "def provide_removable(ctx):\n"
    "    ctx.register('evidence:gitdiff-removable@1', ProducerSpec(\n"
    "        evidence_type='custom:rm', public_ref='evidence:rm@1',\n"
    "        producer_surfaces=('tool_host',)))\n"
)


def _write_pack(root: Path, name: str, pack_id: str, body: str) -> None:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "impl.py").write_text(_IMPL_BODY)
    (pack_dir / "pack.toml").write_text(
        f"packId = {pack_id!r}\ndisplayName = {pack_id!r}\nversion = \"0.0.1\"\n\n" + body
    )


def test_user_evidence_producer_add_override_remove(tmp_path: Path, monkeypatch) -> None:
    user_root = tmp_path / "user_packs"
    _write_pack(
        user_root, "user_ev", "user.ev",
        "[[provides]]\ntype = \"evidence_producer\"\nref = \"evidence:custom-snap@1\"\n"
        "impl = \"user_ev.impl:provide_custom\"\n\n"
        # F-4: canonical ref ``evidence:git-diff@1``.
        "[[provides]]\ntype = \"evidence_producer\"\nref = \"evidence:git-diff@1\"\n"
        "impl = \"user_ev.impl:provide_gitdiff_override\"\n",
    )
    _write_pack(
        user_root, "user_ev_rm", "user.ev-rm",
        "[[provides]]\ntype = \"evidence_producer\"\nref = \"evidence:gitdiff-removable@1\"\n"
        "impl = \"user_ev_rm.impl:provide_removable\"\n",
    )
    monkeypatch.syspath_prepend(str(user_root))
    config_path = tmp_path / "config.toml"
    config_path.write_text('[packs]\ndisable = ["user.ev-rm"]\n')
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))

    registries, _ = load_into_registries([_FIRST_PARTY_ROOT, user_root])

    assert registries.evidence_producers.resolve("evidence:custom-snap@1") is not None  # ADD
    assert (
        registries.evidence_producers.resolve("evidence:git-diff@1").public_ref
        == "evidence:gitDiffV2@1"
    )  # OVERRIDE (F-4: canonical ref ``evidence:git-diff@1``)
    assert registries.evidence_producers.resolve("evidence:gitdiff-removable@1") is None  # REMOVE
