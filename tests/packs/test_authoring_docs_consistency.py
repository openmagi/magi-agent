"""Pack B2 — authoring docs exist and reference the REAL shipped schema/ABI.

Mirrors the env-reference drift-gate idea (scripts/check_env_reference.sh) at
doc granularity: every documented manifest field / context class is pinned to
the live pydantic models so the authoring docs cannot rot silently.
"""
from __future__ import annotations

from pathlib import Path

import magi_agent
from magi_agent.packs.manifest import PackManifest, ProvidesEntry

_DOCS = Path(magi_agent.__file__).resolve().parent.parent / "docs"


def _alias_or_name(model: type) -> set[str]:
    return {
        (field.alias or name) for name, field in model.model_fields.items()
    }


def test_manifest_reference_covers_every_real_field() -> None:
    text = (_DOCS / "pack-manifest-reference.md").read_text()
    for field in sorted(_alias_or_name(PackManifest) | _alias_or_name(ProvidesEntry)):
        assert f"`{field}`" in text, f"pack-manifest-reference.md missing `{field}`"


def test_context_reference_covers_every_provides_type_and_real_classes() -> None:
    from magi_agent.packs import context as ctx
    from magi_agent.packs.context import PrimitiveType

    text = (_DOCS / "pack-context-reference.md").read_text()
    for ptype in PrimitiveType:
        assert ptype.value in text, f"missing provides type {ptype.value}"
    for cls in (
        "ToolProvideContext", "CallbackProvideContext", "ValidatorCtx",
        "HarnessProvideContext", "ControlPlaneProvideContext",
        "EvidenceProducerProvideContext", "RecipeProvideContext",
        "ConnectorProvideContext", "ControlPlaneContext", "Capability",
        "BeforeToolCtx", "AfterToolCtx", "BeforeModelCtx", "AfterAgentCtx",
    ):
        assert hasattr(ctx, cls), f"{cls} no longer exists in packs/context.py"
        assert f"`{cls}`" in text, f"pack-context-reference.md missing `{cls}`"


def test_context_reference_capability_tokens_match_the_enum() -> None:
    from magi_agent.packs.context import Capability

    text = (_DOCS / "pack-context-reference.md").read_text()
    for token in Capability:
        assert f"`{token.value}`" in text, (
            f"pack-context-reference.md missing capability token `{token.value}`"
        )


def test_authoring_walkthrough_uses_the_zero_setup_user_cp_shape() -> None:
    text = (_DOCS / "pack-authoring.md").read_text()
    assert "user_cp.impl:provide" in text
    assert "control_plane:user-extra@1" in text
    assert "magi pack new" in text


def test_docs_manifest_registers_the_three_authoring_pages() -> None:
    import json

    manifest = json.loads((_DOCS / "manifest.json").read_text())
    slugs = {page["slug"] for page in manifest["pages"]}
    for slug in ("pack-authoring", "pack-manifest-reference", "pack-context-reference"):
        assert slug in slugs, f"docs/manifest.json missing page slug {slug!r}"
    assert len(slugs) == len(manifest["pages"]), "duplicate slug in docs/manifest.json"


def test_llms_index_lists_the_three_authoring_pages() -> None:
    text = (_DOCS / "llms.txt").read_text()
    for slug in ("pack-authoring", "pack-manifest-reference", "pack-context-reference"):
        assert f"https://openmagi.ai/docs/{slug}" in text, (
            f"docs/llms.txt missing the {slug} page"
        )


def test_cli_reference_documents_magi_pack_new() -> None:
    from magi_agent.packs.scaffold import PACK_TYPES

    text = (_DOCS / "cli" / "magi.md").read_text()
    assert "## `magi pack`" in text
    assert "magi pack new" in text
    for ptype in PACK_TYPES:
        assert f"`{ptype}`" in text, f"docs/cli/magi.md missing pack type `{ptype}`"
