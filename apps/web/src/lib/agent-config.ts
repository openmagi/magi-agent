export const SUPPORTED_BUILTIN_PRESET_IDS = [
  "answer-quality",
  "completion-evidence",
  "pre-refusal",
  "output-purity",
  "deferral-blocker",
  "fact-grounding",
  "self-claim",
  "resource-existence",
  "claim-citation",
  "deterministic-evidence",
  "coding-verification",
  "coding-context",
  "coding-workspace-lock",
  "coding-child-review",
  "benchmark-verifier",
  "task-contract",
  "goal-progress",
  "task-board-completion",
  "output-delivery",
  "artifact-delivery",
  "response-language",
  "parallel-research",
  "source-authority",
  "memory-continuity",
] as const;

export type SupportedBuiltinPresetId = typeof SUPPORTED_BUILTIN_PRESET_IDS[number];

export const BUILTIN_PRESET_MODES = ["hybrid", "deterministic", "llm"] as const;

export type BuiltinPresetMode = typeof BUILTIN_PRESET_MODES[number];

export interface BuiltinPresetConfig {
  enabled: boolean;
  mode: BuiltinPresetMode;
}

export type BuiltinPresetConfigs = Partial<Record<SupportedBuiltinPresetId, BuiltinPresetConfig>>;

export type AgentConfig = Record<string, unknown> & {
  builtin_presets?: BuiltinPresetConfigs;
  disable_builtin_hooks?: string[];
};

export const BUILTIN_PRESET_HOOK_IDS: Record<SupportedBuiltinPresetId, readonly string[]> = {
  "answer-quality": ["builtin:answer-verifier"],
  "completion-evidence": ["builtin:completion-evidence-gate"],
  "pre-refusal": ["builtin:pre-refusal-verifier"],
  "output-purity": ["builtin:output-purity-gate"],
  "deferral-blocker": ["builtin:deferral-blocker"],
  "fact-grounding": ["builtin:fact-grounding-verifier"],
  "self-claim": ["builtin:self-claim-verifier"],
  "resource-existence": ["builtin:resource-existence-checker"],
  "claim-citation": ["builtin:claim-citation-gate"],
  "deterministic-evidence": ["builtin:deterministic-evidence-verifier"],
  "coding-verification": ["builtin:coding-verification-gate"],
  "coding-context": ["builtin:coding-context-injector"],
  "coding-workspace-lock": ["builtin:coding-workspace-lock", "builtin:coding-unit-completion-gate"],
  "coding-child-review": ["builtin:coding-child-review-gate"],
  "benchmark-verifier": ["builtin:benchmark-verifier"],
  "task-contract": ["builtin:task-contract-gate"],
  "goal-progress": ["builtin:goal-progress-gate"],
  "task-board-completion": ["builtin:task-board-completion-gate"],
  "output-delivery": ["builtin:output-delivery-gate"],
  "artifact-delivery": ["builtin:artifact-delivery-gate"],
  "response-language": ["builtin:response-language-gate"],
  "parallel-research": ["builtin:parallel-research-gate"],
  "source-authority": ["builtin:source-authority-gate"],
  "memory-continuity": ["builtin:memory-continuity-guard"],
};

const supportedBuiltinPresetIdSet = new Set<string>(SUPPORTED_BUILTIN_PRESET_IDS);
const builtinPresetModeSet = new Set<string>(BUILTIN_PRESET_MODES);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isSupportedBuiltinPresetId(value: string): value is SupportedBuiltinPresetId {
  return supportedBuiltinPresetIdSet.has(value);
}

export function normalizeBuiltinPresetConfigs(input: unknown): BuiltinPresetConfigs {
  const configs: BuiltinPresetConfigs = {};
  if (!isPlainObject(input)) return configs;

  for (const id of SUPPORTED_BUILTIN_PRESET_IDS) {
    const raw = input[id];
    if (!isPlainObject(raw)) continue;
    if (typeof raw.enabled !== "boolean") continue;
    if (typeof raw.mode !== "string" || !builtinPresetModeSet.has(raw.mode)) continue;
    configs[id] = { enabled: raw.enabled, mode: raw.mode as BuiltinPresetMode };
  }

  return configs;
}

export function normalizeAgentConfig(input: unknown): AgentConfig | undefined {
  if (!isPlainObject(input)) return undefined;

  const config: AgentConfig = { ...input };
  if ("builtin_presets" in config) {
    const presets = normalizeBuiltinPresetConfigs(config.builtin_presets);
    if (Object.keys(presets).length > 0) {
      config.builtin_presets = presets;
    } else {
      delete config.builtin_presets;
    }
  }

  return config;
}

function stringArray(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  return input
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim());
}

function mergeDisableBuiltinHooks(
  existing: unknown,
  builtinPresets: BuiltinPresetConfigs,
): string[] {
  const controlledHookIds = new Set<string>(
    Object.keys(builtinPresets).flatMap((presetId) => (
      isSupportedBuiltinPresetId(presetId) ? [...BUILTIN_PRESET_HOOK_IDS[presetId]] : []
    )),
  );
  const preserved = stringArray(existing).filter((hookId) => !controlledHookIds.has(hookId));
  const disabledFromPresets = SUPPORTED_BUILTIN_PRESET_IDS.flatMap((presetId) => {
    const config = builtinPresets[presetId];
    return config?.enabled === false ? [...BUILTIN_PRESET_HOOK_IDS[presetId]] : [];
  });
  return [...new Set([...preserved, ...disabledFromPresets])];
}

export function mergeAgentConfigBuiltinPresets(
  initialAgentConfig: unknown,
  builtinPresets: unknown,
): AgentConfig {
  const config = normalizeAgentConfig(initialAgentConfig) ?? {};
  const mergedPresets = {
    ...normalizeBuiltinPresetConfigs(config.builtin_presets),
    ...normalizeBuiltinPresetConfigs(builtinPresets),
  };
  const disableBuiltinHooks = mergeDisableBuiltinHooks(config.disable_builtin_hooks, mergedPresets);
  const nextConfig: AgentConfig = {
    ...config,
    builtin_presets: mergedPresets,
  };
  if (disableBuiltinHooks.length > 0) {
    nextConfig.disable_builtin_hooks = disableBuiltinHooks;
  } else {
    delete nextConfig.disable_builtin_hooks;
  }
  return {
    ...nextConfig,
  };
}
