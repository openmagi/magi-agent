"""E-12 — tool-schema repair moves from ``prompt/`` to ``adk_bridge/``.

Schema repair for the typed Gemini schema path (non-string enums →
strings, additional-properties keys dropped) is a tool/adk-bridge
concern; it should not live in the prompt-assembly package. This test
locks the new home (``adk_bridge/tool_schema_repair.py``) and the
back-compat re-export from ``prompt/provider_adapter`` so any external
importer keeps working without an immediate flag-day.
"""

from __future__ import annotations

from pathlib import Path

from magi_agent.shared.provider_family import ProviderFamily


# ---------------------------------------------------------------------------
# Definition moved: import the canonical home and verify it works.
# ---------------------------------------------------------------------------


def test_canonical_module_houses_repair_function() -> None:
    from magi_agent.adk_bridge import tool_schema_repair

    assert callable(tool_schema_repair.repair_tool_schema_for_provider)
    assert callable(tool_schema_repair._repair_gemini_schema)
    assert callable(tool_schema_repair._enum_value_to_string)


def test_gemini_enum_coercion_via_canonical_home() -> None:
    from magi_agent.adk_bridge.tool_schema_repair import (
        repair_tool_schema_for_provider,
    )

    schema = {"type": "integer", "enum": [1, 2, 3]}
    out = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
    assert out["enum"] == ["1", "2", "3"]
    assert out["type"] == "string"


def test_gemini_additional_properties_dropped_via_canonical_home() -> None:
    from magi_agent.adk_bridge.tool_schema_repair import (
        repair_tool_schema_for_provider,
    )

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"type": "string"}},
    }
    out = repair_tool_schema_for_provider(schema, ProviderFamily.GOOGLE)
    assert "additionalProperties" not in out


def test_non_google_family_is_identity_via_canonical_home() -> None:
    from magi_agent.adk_bridge.tool_schema_repair import (
        repair_tool_schema_for_provider,
    )

    schema = {"type": "integer", "enum": [1, 2, 3]}
    out = repair_tool_schema_for_provider(schema, ProviderFamily.ANTHROPIC)
    assert out is schema  # contract: input returned as-is for non-Google


# ---------------------------------------------------------------------------
# Back-compat: importing the legacy ``prompt/provider_adapter`` symbol must
# still resolve to the same function object as the canonical home.
# ---------------------------------------------------------------------------


def test_legacy_prompt_re_export_is_same_function_object() -> None:
    from magi_agent.adk_bridge.tool_schema_repair import (
        repair_tool_schema_for_provider as canonical,
    )
    from magi_agent.prompt.provider_adapter import (
        repair_tool_schema_for_provider as legacy,
    )

    assert legacy is canonical


def test_prompt_package_re_export_is_same_function_object() -> None:
    from magi_agent.adk_bridge.tool_schema_repair import (
        repair_tool_schema_for_provider as canonical,
    )

    # The public ``magi_agent.prompt`` surface (PR9) historically
    # exported repair_tool_schema_for_provider. Re-export must continue
    # to resolve to the canonical function.
    from magi_agent.prompt.provider_adapter import (
        repair_tool_schema_for_provider as via_module,
    )

    assert via_module is canonical


# ---------------------------------------------------------------------------
# Meta-test: ``prompt/`` may no longer DEFINE the schema-repair symbols
# (only re-export them).
# ---------------------------------------------------------------------------


def test_prompt_provider_adapter_no_longer_defines_repair_symbols() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    target = package_root / "prompt" / "provider_adapter.py"
    text = target.read_text(encoding="utf-8")
    forbidden = (
        "def repair_tool_schema_for_provider(",
        "def _repair_gemini_schema(",
        "def _enum_value_to_string(",
        "_GEMINI_DROPPED_SCHEMA_KEYS",
    )
    offenders = [token for token in forbidden if token in text]
    # ``_GEMINI_DROPPED_SCHEMA_KEYS`` can appear as a re-export bare name;
    # the bug is only the literal `frozenset(...)` definition line — but
    # since prompt/ doesn't need the symbol at all post-move, forbid any
    # mention. (The shim re-exports go through `repair_tool_schema_for_provider`.)
    assert offenders == [], (
        "prompt/provider_adapter.py still references moved schema-repair "
        "symbols. Only re-exports are allowed (no definitions, no constants). "
        f"Offenders: {offenders}"
    )


def test_only_adk_bridge_houses_schema_repair_definition() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    canonical = {"tool_schema_repair.py"}
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        if "tests" in path.relative_to(package_root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "def repair_tool_schema_for_provider(" in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Second definition of ``repair_tool_schema_for_provider`` outside "
        "``adk_bridge/tool_schema_repair.py``. "
        f"Offenders: {offenders}"
    )
