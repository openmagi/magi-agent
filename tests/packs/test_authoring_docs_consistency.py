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
