// Curated model presets for the self-hosted Local Runtime settings, per provider.
//
// Labels mirror the hosted model catalog (src/lib/constants.ts MODEL_LABELS /
// model-options.ts) so the names match what users see elsewhere. VALUES are the
// raw model ids the LOCAL CLI resolver feeds to LiteLlm — i.e. the bare model
// without the `<provider>/` litellm prefix (see magi_agent/cli/providers.py:
// the prefix is applied by ProviderConfig.litellm_model).
//
// Source of truth: magi_agent/models/builtin_catalog.json (E-1). The actual
// preset table + per-provider defaults are GENERATED into
// ./generated-local-runtime-models.ts by
// `python -m magi_agent.models.export_ts`. Re-run that command after editing
// the JSON; a Python test enforces the generated file is up to date.
//
// This file keeps the hand-written UI helpers (the custom-sentinel type and
// the `isPresetModel` boolean check) and re-exports the generated tables.

import {
  GENERATED_LOCAL_RUNTIME_DEFAULT_MODEL,
  GENERATED_LOCAL_RUNTIME_MODEL_PRESETS,
  type LocalRuntimeProvider as GeneratedLocalRuntimeProvider,
  type LocalRuntimeModelOption as GeneratedLocalRuntimeModelOption,
} from "./generated-local-runtime-models";

export type LocalRuntimeProvider = GeneratedLocalRuntimeProvider;
export type LocalRuntimeModelOption = GeneratedLocalRuntimeModelOption;

/** Sentinel select value that reveals the free-text model input. */
export const CUSTOM_MODEL_VALUE = "__custom__";

/** Per-provider preset list (catalog source=direct or router). */
export const LOCAL_RUNTIME_MODEL_PRESETS: Record<
  LocalRuntimeProvider,
  readonly LocalRuntimeModelOption[]
> = GENERATED_LOCAL_RUNTIME_MODEL_PRESETS;

/** Per-provider default model (mirrors magi_agent/cli/providers.py _DEFAULT_MODEL). */
export const LOCAL_RUNTIME_DEFAULT_MODEL: Record<LocalRuntimeProvider, string> =
  GENERATED_LOCAL_RUNTIME_DEFAULT_MODEL;

export function isPresetModel(provider: LocalRuntimeProvider, model: string): boolean {
  return LOCAL_RUNTIME_MODEL_PRESETS[provider].some((option) => option.value === model);
}
