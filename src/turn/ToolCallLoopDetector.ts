import crypto from "node:crypto";

const EXCLUDED_FIELDS = new Set(["task_progress", "progress", "metadata"]);

export interface LoopDetectorConfig {
  softThreshold?: number;
  hardThreshold?: number;
  frequencySoftThreshold?: number;
  frequencyHardThreshold?: number;
}

export type LoopAction = "ok" | "soft_warning" | "hard_escalation";

export interface LoopCheckResult {
  action: LoopAction;
  count: number;
  hash: string;
  frequencyCount?: number;
}

export class ToolCallLoopDetector {
  private lastHash: string | null = null;
  private consecutiveCount = 0;
  private readonly softThreshold: number;
  private readonly hardThreshold: number;
  private readonly frequencySoftThreshold: number;
  private readonly frequencyHardThreshold: number;
  private readonly toolNameCounts = new Map<string, number>();

  constructor(config: LoopDetectorConfig = {}) {
    this.softThreshold = config.softThreshold ?? 3;
    this.hardThreshold = config.hardThreshold ?? 5;
    this.frequencySoftThreshold = config.frequencySoftThreshold ?? 15;
    this.frequencyHardThreshold = config.frequencyHardThreshold ?? 30;
  }

  static hashCall(toolName: string, input: unknown): string {
    const stripped = stripExcludedFields(input);
    const raw = `${toolName}:${JSON.stringify(stripped)}`;
    return crypto.createHash("sha256").update(raw).digest("hex").slice(0, 16);
  }

  check(toolName: string, input: unknown): LoopCheckResult {
    const hash = ToolCallLoopDetector.hashCall(toolName, input);

    if (hash === this.lastHash) {
      this.consecutiveCount++;
    } else {
      this.lastHash = hash;
      this.consecutiveCount = 1;
    }

    const nameCount = (this.toolNameCounts.get(toolName) ?? 0) + 1;
    this.toolNameCounts.set(toolName, nameCount);

    let action: LoopAction = "ok";
    let frequencyCount: number | undefined;

    if (this.consecutiveCount >= this.hardThreshold) {
      action = "hard_escalation";
    } else if (nameCount >= this.frequencyHardThreshold) {
      action = "hard_escalation";
      frequencyCount = nameCount;
    } else if (this.consecutiveCount >= this.softThreshold) {
      action = "soft_warning";
    } else if (nameCount >= this.frequencySoftThreshold) {
      action = "soft_warning";
      frequencyCount = nameCount;
    }

    return { action, count: this.consecutiveCount, hash, ...(frequencyCount !== undefined ? { frequencyCount } : {}) };
  }

  reset(): void {
    this.lastHash = null;
    this.consecutiveCount = 0;
    this.toolNameCounts.clear();
  }

  getToolNameCount(toolName: string): number {
    return this.toolNameCounts.get(toolName) ?? 0;
  }
}

function stripExcludedFields(input: unknown): unknown {
  if (!input || typeof input !== "object" || Array.isArray(input)) return input;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(input as Record<string, unknown>)) {
    if (!EXCLUDED_FIELDS.has(k)) out[k] = v;
  }
  return out;
}
