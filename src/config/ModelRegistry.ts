import { existsSync, readFileSync, watch, type FSWatcher } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { load } from "js-yaml";

export interface ModelThinking {
  type: string;
  budgetTokens?: number;
}

export interface ModelPricing {
  inputPerMtok: number;
  outputPerMtok: number;
}

export interface ModelCapability {
  id: string;
  provider: string;
  contextWindow: number;
  maxOutput: number;
  thinking?: ModelThinking;
  temperature: number;
  capabilities: string[];
  editFormat: string;
  pricing: ModelPricing;
  providerParams?: Record<string, unknown>;
  aliases: string[];
}

export interface ModelRegistryFilter {
  provider?: string;
  capability?: string;
}

export interface ModelRegistryOptions {
  path?: string;
  watch?: boolean;
  debounceMs?: number;
  logger?: Pick<Console, "error" | "warn">;
}

interface RawRegistry {
  models?: Record<string, RawModelEntry>;
}

interface RawModelEntry {
  provider?: unknown;
  aliases?: unknown;
  context_window?: unknown;
  max_output?: unknown;
  max_output_tokens?: unknown;
  supports_thinking?: unknown;
  thinking?: unknown;
  thinking_mode?: unknown;
  temperature?: unknown;
  capabilities?: unknown;
  edit_format?: unknown;
  pricing?: unknown;
  provider_params?: unknown;
}

interface RawPricing {
  input_per_mtok?: unknown;
  input_usd_per_mtok?: unknown;
  output_per_mtok?: unknown;
  output_usd_per_mtok?: unknown;
}

const ENABLED_VALUES = new Set(["1", "true", "yes", "on"]);

export function isModelRegistryEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return ENABLED_VALUES.has((env.MAGI_MODEL_REGISTRY ?? "").trim().toLowerCase());
}

export function defaultModelRegistryPath(env: NodeJS.ProcessEnv = process.env): string {
  return env.MODEL_REGISTRY_PATH?.trim() || resolve(process.cwd(), "config/model-registry.yaml");
}

export class ModelRegistry {
  private readonly registryPath: string;
  private readonly debounceMs: number;
  private readonly logger: Pick<Console, "error" | "warn">;
  private canonicalModels = new Map<string, ModelCapability>();
  private lookup = new Map<string, ModelCapability>();
  private watchers: FSWatcher[] = [];
  private reloadTimer: NodeJS.Timeout | null = null;

  constructor(options: ModelRegistryOptions = {}) {
    this.registryPath = resolve(options.path ?? defaultModelRegistryPath());
    this.debounceMs = options.debounceMs ?? 100;
    this.logger = options.logger ?? console;
    this.reload();
    if (options.watch) this.startWatching();
  }

  get path(): string {
    return this.registryPath;
  }

  getModel(modelId: string): ModelCapability | undefined {
    return this.lookup.get(modelId);
  }

  listModels(filter: ModelRegistryFilter = {}): ModelCapability[] {
    return [...this.canonicalModels.values()].filter((model) => {
      if (filter.provider && model.provider !== filter.provider) return false;
      if (filter.capability && !model.capabilities.includes(filter.capability)) return false;
      return true;
    });
  }

  reload(): boolean {
    if (!existsSync(this.registryPath)) {
      if (this.canonicalModels.size > 0) {
        this.logger.warn("[ModelRegistry] model registry path disappeared; keeping previous values");
        return false;
      }
      this.swapModels(new Map());
      return true;
    }

    try {
      const rawText = readFileSync(this.registryPath, "utf8");
      const parsed = load(rawText) as RawRegistry | null;
      const models = buildModels(parsed);
      this.swapModels(models);
      return true;
    } catch (error) {
      this.logger.error("[ModelRegistry] failed to load model registry; keeping previous values", error);
      return false;
    }
  }

  close(): void {
    if (this.reloadTimer) {
      clearTimeout(this.reloadTimer);
      this.reloadTimer = null;
    }
    for (const watcher of this.watchers) watcher.close();
    this.watchers = [];
  }

  private startWatching(): void {
    const directory = dirname(this.registryPath);
    const targetName = basename(this.registryPath);
    if (!existsSync(directory)) return;

    const scheduleReload = (): void => {
      if (this.reloadTimer) clearTimeout(this.reloadTimer);
      this.reloadTimer = setTimeout(() => {
        this.reloadTimer = null;
        this.reload();
      }, this.debounceMs);
    };

    this.watchers.push(watch(directory, (_eventType, filename) => {
      if (filename && filename.toString() !== targetName) return;
      scheduleReload();
    }));

    if (existsSync(this.registryPath)) {
      this.watchers.push(watch(this.registryPath, scheduleReload));
    }
  }

  private swapModels(models: Map<string, ModelCapability>): void {
    const lookup = new Map<string, ModelCapability>();
    for (const model of models.values()) {
      lookup.set(model.id, model);
      for (const alias of model.aliases) lookup.set(alias, model);
    }
    this.canonicalModels = models;
    this.lookup = lookup;
  }
}

function buildModels(raw: RawRegistry | null): Map<string, ModelCapability> {
  const models = new Map<string, ModelCapability>();
  if (!raw || typeof raw !== "object" || !raw.models) return models;
  if (typeof raw.models !== "object" || Array.isArray(raw.models)) {
    throw new Error("model registry `models` must be an object");
  }

  for (const [id, entry] of Object.entries(raw.models)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(`model ${id} must be an object`);
    }
    models.set(id, normalizeEntry(id, entry));
  }
  return models;
}

function normalizeEntry(id: string, entry: RawModelEntry): ModelCapability {
  const provider = requiredString(entry.provider, `${id}.provider`);
  const aliases = uniqueStrings([
    ...optionalStringArray(entry.aliases, `${id}.aliases`),
    ...(id.includes("/") || !provider ? [] : [`${provider}/${id}`]),
  ]).filter((alias) => alias !== id);
  const thinking = normalizeThinking(entry);
  const pricing = normalizePricing(id, entry.pricing);
  const providerParams = optionalRecord(entry.provider_params, `${id}.provider_params`);

  return stripUndefined({
    id,
    provider,
    contextWindow: requiredNumber(entry.context_window, `${id}.context_window`),
    maxOutput: requiredNumber(entry.max_output ?? entry.max_output_tokens, `${id}.max_output`),
    thinking,
    temperature: optionalNumber(entry.temperature, `${id}.temperature`) ?? 1,
    capabilities: optionalStringArray(entry.capabilities, `${id}.capabilities`),
    editFormat: optionalString(entry.edit_format, `${id}.edit_format`) ?? "whole",
    pricing,
    providerParams,
    aliases,
  });
}

function normalizeThinking(entry: RawModelEntry): ModelThinking | undefined {
  if (entry.thinking && typeof entry.thinking === "object" && !Array.isArray(entry.thinking)) {
    const thinking = entry.thinking as Record<string, unknown>;
    const type = optionalString(thinking.type, "thinking.type");
    if (!type || type === "none") return undefined;
    const budgetTokens = optionalNumber(
      thinking.budgetTokens ?? thinking.budget_tokens,
      "thinking.budget_tokens",
    );
    return stripUndefined({ type, budgetTokens });
  }

  const thinkingMode = optionalString(entry.thinking_mode, "thinking_mode");
  if (thinkingMode && thinkingMode !== "none") return { type: thinkingMode };
  if (entry.supports_thinking === true) return { type: "adaptive" };
  return undefined;
}

function normalizePricing(id: string, rawPricing: unknown): ModelPricing {
  if (!rawPricing || typeof rawPricing !== "object" || Array.isArray(rawPricing)) {
    throw new Error(`model ${id} pricing must be an object`);
  }
  const pricing = rawPricing as RawPricing;
  return {
    inputPerMtok: requiredNumber(
      pricing.input_per_mtok ?? pricing.input_usd_per_mtok,
      `${id}.pricing.input_per_mtok`,
    ),
    outputPerMtok: requiredNumber(
      pricing.output_per_mtok ?? pricing.output_usd_per_mtok,
      `${id}.pricing.output_per_mtok`,
    ),
  };
}

function requiredString(value: unknown, field: string): string {
  const parsed = optionalString(value, field);
  if (!parsed) throw new Error(`${field} must be a non-empty string`);
  return parsed;
}

function optionalString(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new Error(`${field} must be a string`);
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function requiredNumber(value: unknown, field: string): number {
  const parsed = optionalNumber(value, field);
  if (parsed === undefined) throw new Error(`${field} must be a number`);
  return parsed;
}

function optionalNumber(value: unknown, field: string): number | undefined {
  if (value === undefined || value === null) return undefined;
  const parsed = typeof value === "string" ? Number(value.replace(/_/g, "")) : value;
  if (typeof parsed !== "number" || !Number.isFinite(parsed)) {
    throw new Error(`${field} must be a finite number`);
  }
  return parsed;
}

function optionalStringArray(value: unknown, field: string): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error(`${field} must be an array`);
  return value.map((item, index) => requiredString(item, `${field}[${index}]`));
}

function optionalRecord(value: unknown, field: string): Record<string, unknown> | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values)];
}

function stripUndefined<T extends Record<string, unknown>>(value: T): T {
  for (const key of Object.keys(value)) {
    if (value[key] === undefined) delete value[key];
  }
  return value;
}
