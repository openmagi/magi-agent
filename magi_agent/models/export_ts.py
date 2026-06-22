"""Render ``apps/web/src/lib/models/generated-local-runtime-models.ts``.

The catalog is the single source of truth (E-1); the dashboard's preset list +
``DEFAULT_LOCAL_MODELS`` are derived here so the TS and Python always agree.

Usage::

    python -m magi_agent.models.export_ts \\
        --out apps/web/src/lib/models/generated-local-runtime-models.ts

A packaged test (``tests/models/test_catalog_export.py``) asserts the committed
file matches a fresh render, mirroring the supabase-types drop-safety gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from magi_agent.models.catalog import ModelCatalog
from magi_agent.models.types import ModelRecord

# Providers the dashboard offers (mirrors ``apps/web/src/lib/models/
# local-runtime-models.ts:LocalRuntimeProvider``).
_DASHBOARD_PROVIDERS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "gemini",
    "fireworks",
    "openrouter",
)


def _label_with_via_suffix(record: ModelRecord) -> str:
    """OpenRouter records keep the ``(via OpenRouter)`` suffix as today."""
    if record.source == "router" and "OpenRouter" not in record.label:
        return f"{record.label} (via OpenRouter)"
    return record.label


def _records_for_provider(
    catalog: ModelCatalog, provider: str
) -> list[ModelRecord]:
    """Pick the records the dashboard should offer as presets for ``provider``.

    Strategy: every ``source in {direct, router}`` record under that provider,
    excluding ``deprecated=False`` ones we are sure to retire — but KEEP
    deprecated records on the dashboard so configs that saved an old id don't
    suddenly become "unknown model" (mirrors the legacy local-runtime-models
    backward-compat note about ``claude-opus-4-6``).
    """
    out: list[ModelRecord] = []
    for r in catalog.all_records():
        if r.provider != provider:
            continue
        if r.source not in {"direct", "router"}:
            continue
        out.append(r)
    return out


def _default_for_provider(catalog: ModelCatalog, provider: str) -> ModelRecord:
    """The provider's user-facing default id (highest non-deprecated tier)."""
    return catalog.default_model_for(provider)


def _ts_string(value: str) -> str:
    """Emit a JSON-string literal, which is valid TS too."""
    return json.dumps(value, ensure_ascii=False)


def render(catalog: ModelCatalog | None = None) -> str:
    """Render the generated TS file content.

    Pure function for testing — no I/O. Returns the full file text including
    the generated-header comment.
    """
    if catalog is None:
        catalog = ModelCatalog.builtin()

    lines: list[str] = []
    lines.append("// DO NOT EDIT — generated from magi_agent/models/builtin_catalog.json")
    lines.append("// Re-run: python -m magi_agent.models.export_ts \\")
    lines.append("//   --out apps/web/src/lib/models/generated-local-runtime-models.ts")
    lines.append("")
    lines.append("export type LocalRuntimeProvider =")
    lines.append(
        '  | "anthropic"\n  | "openai"\n  | "gemini"\n  | "fireworks"\n  | "openrouter";'
    )
    lines.append("")
    lines.append("export interface LocalRuntimeModelOption {")
    lines.append("  value: string;")
    lines.append("  label: string;")
    lines.append("}")
    lines.append("")
    lines.append("/** Per-provider preset list (catalog source=direct or router). */")
    lines.append(
        "export const GENERATED_LOCAL_RUNTIME_MODEL_PRESETS: Record<"
    )
    lines.append("  LocalRuntimeProvider,")
    lines.append("  readonly LocalRuntimeModelOption[]")
    lines.append("> = {")
    for provider in _DASHBOARD_PROVIDERS:
        records = _records_for_provider(catalog, provider)
        lines.append(f"  {provider}: [")
        for r in records:
            value = _ts_string(r.model)
            label = _ts_string(_label_with_via_suffix(r))
            suffix = ""
            if r.deprecated:
                suffix = "  // deprecated (kept for backward compat)"
            lines.append(f"    {{ value: {value}, label: {label} }},{suffix}")
        lines.append("  ],")
    lines.append("};")
    lines.append("")
    lines.append("/** Per-provider default model id, sourced from the catalog. */")
    lines.append(
        "export const GENERATED_LOCAL_RUNTIME_DEFAULT_MODEL: "
        "Record<LocalRuntimeProvider, string> = {"
    )
    for provider in _DASHBOARD_PROVIDERS:
        default = _default_for_provider(catalog, provider)
        lines.append(f"  {provider}: {_ts_string(default.model)},")
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export ModelCatalog to TypeScript.")
    parser.add_argument(
        "--out",
        required=True,
        help="Path to the generated .ts file.",
    )
    args = parser.parse_args(argv)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover — module CLI entry
    sys.exit(main())
