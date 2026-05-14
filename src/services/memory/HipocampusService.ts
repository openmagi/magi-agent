import fs from "node:fs/promises";
import path from "node:path";
import { CompactionEngine, type CompactionConfig, type CompactionLLM, type CompactionResult } from "./CompactionEngine.js";
import { QmdManager, type QmdSearchResult } from "./QmdManager.js";

export interface RootMemory {
  path: "memory/ROOT.md" | "MEMORY.md";
  content: string;
  bytes: number;
}

export interface HipocampusRecallResult {
  root: RootMemory | null;
  results: QmdSearchResult[];
}

export interface HipocampusStatus {
  qmdReady: boolean;
  vectorEnabled: boolean;
  compactionConfigured: boolean;
  cooldownHours: number | null;
  rootMaxTokens: number | null;
  lastCompactionRun: string | null;
  rootMemory: {
    path: string | null;
    bytes: number;
    loaded: boolean;
  };
}

export interface HipocampusServiceDeps {
  workspaceRoot: string;
  defaultModel: string;
  llm: CompactionLLM;
  qmdManager?: QmdManagerLike;
  loadConfig?: (
    workspaceRoot: string,
    defaultModel: string,
  ) => Promise<CompactionConfig>;
  createCompactionEngine?: (
    workspaceRoot: string,
    config: CompactionConfig,
    llm: CompactionLLM,
  ) => CompactionEngineLike;
}

export interface QmdManagerLike {
  start(): Promise<void>;
  stop?(): Promise<void>;
  isReady(): boolean;
  search(
    query: string,
    opts?: { collection?: string; limit?: number; minScore?: number },
  ): Promise<QmdSearchResult[]>;
  hybridSearch?(
    query: string,
    opts?: { collection?: string; limit?: number; minScore?: number },
  ): Promise<QmdSearchResult[]>;
  reindex(): Promise<void>;
}

export interface CompactionEngineLike {
  run(force?: boolean): Promise<CompactionResult>;
}

interface CompactionState {
  lastCompactionRun: string | null;
}

export class HipocampusService {
  private readonly workspaceRoot: string;
  private readonly defaultModel: string;
  private readonly llm: CompactionLLM;
  private readonly qmdManager: QmdManagerLike;
  private readonly loadConfigFn: (
    workspaceRoot: string,
    defaultModel: string,
  ) => Promise<CompactionConfig>;
  private readonly createCompactionEngineFn: (
    workspaceRoot: string,
    config: CompactionConfig,
    llm: CompactionLLM,
  ) => CompactionEngineLike;
  private compactionConfig: CompactionConfig | null = null;
  private compactionEngine: CompactionEngineLike | null = null;

  constructor(deps: HipocampusServiceDeps) {
    this.workspaceRoot = deps.workspaceRoot;
    this.defaultModel = deps.defaultModel;
    this.llm = deps.llm;
    this.qmdManager =
      deps.qmdManager ??
      new QmdManager(
        deps.workspaceRoot,
        (process.env.MAGI_VECTOR_SEARCH ?? "off").trim().toLowerCase() === "on",
      );
    this.loadConfigFn = deps.loadConfig ?? CompactionEngine.loadConfig;
    this.createCompactionEngineFn =
      deps.createCompactionEngine ??
      ((workspaceRoot, config, llm) =>
        new CompactionEngine(workspaceRoot, config, llm));
  }

  async start(): Promise<void> {
    await this.qmdManager.start();
    this.compactionConfig = await this.loadConfigFn(
      this.workspaceRoot,
      this.defaultModel,
    );
    this.compactionEngine = this.createCompactionEngineFn(
      this.workspaceRoot,
      this.compactionConfig,
      this.llm,
    );
  }

  async stop(): Promise<void> {
    await this.qmdManager.stop?.();
  }

  getQmdManager(): QmdManagerLike {
    return this.qmdManager;
  }

  getCompactionEngine(): CompactionEngineLike | null {
    return this.compactionEngine;
  }

  async loadRootMemory(): Promise<RootMemory | null> {
    const candidates: Array<RootMemory["path"]> = ["memory/ROOT.md", "MEMORY.md"];
    for (const rel of candidates) {
      const full = path.join(this.workspaceRoot, rel);
      try {
        const content = await fs.readFile(full, "utf8");
        if (!content.trim()) continue;
        return {
          path: rel,
          content,
          bytes: Buffer.byteLength(content, "utf8"),
        };
      } catch {
        continue;
      }
    }
    return null;
  }

  async recall(
    query: string,
    opts?: { collection?: string; limit?: number; minScore?: number },
  ): Promise<HipocampusRecallResult> {
    const root = await this.loadRootMemory();
    let results: QmdSearchResult[] = [];
    if (this.qmdManager.isReady()) {
      const searchFn = this.qmdManager.hybridSearch
        ? this.qmdManager.hybridSearch.bind(this.qmdManager)
        : this.qmdManager.search.bind(this.qmdManager);
      results = await searchFn(query, opts);
    }
    return { root, results };
  }

  async compact(force?: boolean): Promise<CompactionResult> {
    if (!this.compactionEngine) {
      return {
        skipped: true,
        compacted: false,
        stats: { daily: [], weekly: [], monthly: [] },
      };
    }
    const result = await this.compactionEngine.run(force);
    if (result.compacted) {
      await this.qmdManager.reindex();
    }
    return result;
  }

  async status(): Promise<HipocampusStatus> {
    const root = await this.loadRootMemory();
    const state = await this.readCompactionState();
    return {
      qmdReady: this.qmdManager.isReady(),
      vectorEnabled:
        (process.env.MAGI_VECTOR_SEARCH ?? "off").trim().toLowerCase() === "on",
      compactionConfigured: this.compactionConfig !== null,
      cooldownHours: this.compactionConfig?.cooldownHours ?? null,
      rootMaxTokens: this.compactionConfig?.rootMaxTokens ?? null,
      lastCompactionRun: state.lastCompactionRun,
      rootMemory: {
        path: root?.path ?? null,
        bytes: root?.bytes ?? 0,
        loaded: root !== null,
      },
    };
  }

  private async readCompactionState(): Promise<CompactionState> {
    const statePath = path.join(this.workspaceRoot, "memory", ".compaction-state.json");
    try {
      const raw = await fs.readFile(statePath, "utf8");
      const parsed = JSON.parse(raw) as Partial<CompactionState>;
      return {
        lastCompactionRun:
          typeof parsed.lastCompactionRun === "string"
            ? parsed.lastCompactionRun
            : null,
      };
    } catch {
      return { lastCompactionRun: null };
    }
  }
}
