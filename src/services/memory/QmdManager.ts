import { execFile as execFileCb } from "node:child_process";
import fs from "node:fs/promises";
import { promisify } from "node:util";
import path from "node:path";

const execFileAsync = promisify(execFileCb);

export interface QmdSearchResult {
  path: string;
  content: string;
  score: number;
  context?: string;
}

export interface QmdSearchOpts {
  collection?: string;
  limit?: number;
  minScore?: number;
}

export class QmdManager {
  private ready = false;
  private readonly memoryDir: string;
  private readonly knowledgeDir: string;
  private readonly maxReadConcurrency = 2;
  private activeReads = 0;
  private writerActive = false;
  private readonly queue: Array<{ kind: "read" | "write"; start: () => void }> = [];

  constructor(
    private readonly workspaceRoot: string,
    private readonly vectorEnabled: boolean,
  ) {
    this.memoryDir = path.join(workspaceRoot, "memory");
    this.knowledgeDir = path.join(workspaceRoot, "knowledge");
  }

  isReady(): boolean {
    return this.ready;
  }

  async start(): Promise<void> {
    try {
      await this.withWriteSlot(async () => {
        await fs.mkdir(this.memoryDir, { recursive: true });
        await fs.mkdir(this.knowledgeDir, { recursive: true });
        // Register local workspace collections (silent fail if already present)
        await this.exec(["collection", "add", this.memoryDir, "--name", "memory"]).catch(() => {});
        await this.exec(["collection", "add", this.knowledgeDir, "--name", "knowledge"]).catch(() => {});
        // Initial index build
        await this.exec(["update"]);
        if (this.vectorEnabled) {
          await this.exec(["embed"]);
        }
      });
      this.ready = true;
    } catch {
      this.ready = false;
    }
  }

  async search(query: string, opts?: QmdSearchOpts): Promise<QmdSearchResult[]> {
    if (!this.ready) return [];
    return this.withReadSlot(async () => {
      const collection = opts?.collection ?? "memory";
      const limit = opts?.limit ?? 5;
      const minScore = opts?.minScore ?? 0.3;
      try {
        const { stdout } = await this.exec([
          "search", query,
          "--collection", collection,
          "--limit", String(limit),
          "--min-score", String(minScore),
          "--json",
        ]);
        const parsed = JSON.parse(stdout);
        return Array.isArray(parsed?.results) ? parsed.results : [];
      } catch {
        return [];
      }
    });
  }

  async vectorSearch(query: string, opts?: QmdSearchOpts): Promise<QmdSearchResult[]> {
    if (!this.ready || !this.vectorEnabled) return [];
    return this.withReadSlot(async () => {
      const collection = opts?.collection ?? "memory";
      const limit = opts?.limit ?? 5;
      const minScore = opts?.minScore ?? 0.3;
      try {
        const { stdout } = await this.exec([
          "vsearch", query,
          "--collection", collection,
          "--limit", String(limit),
          "--min-score", String(minScore),
          "--json",
        ]);
        const parsed = JSON.parse(stdout);
        return Array.isArray(parsed?.results) ? parsed.results : [];
      } catch {
        return [];
      }
    });
  }

  /**
   * Hybrid search: runs BM25 + vector in parallel, merges results by
   * path (higher score wins), returns up to `limit` results sorted by
   * score descending. Only available when vectorEnabled — falls back to
   * BM25-only otherwise.
   */
  async hybridSearch(query: string, opts?: QmdSearchOpts): Promise<QmdSearchResult[]> {
    if (!this.ready) return [];
    const limit = opts?.limit ?? 5;

    if (!this.vectorEnabled) {
      return this.search(query, opts);
    }

    // Run BM25 + vector in parallel — each returns up to `limit` results.
    // We request more from each to have a larger candidate pool for merging.
    const expandedOpts = { ...opts, limit: limit + 3 };
    const [bm25, vector] = await Promise.all([
      this.search(query, expandedOpts),
      this.vectorSearch(query, expandedOpts),
    ]);

    // Merge: dedupe by path, keep higher score
    const byPath = new Map<string, QmdSearchResult>();
    for (const r of [...bm25, ...vector]) {
      const existing = byPath.get(r.path);
      if (!existing || r.score > existing.score) {
        byPath.set(r.path, r);
      }
    }

    return Array.from(byPath.values())
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  async reindex(): Promise<void> {
    if (!this.ready) return;
    try {
      await this.withWriteSlot(async () => {
        await this.exec(["update"]);
        if (this.vectorEnabled) {
          await this.exec(["embed"]);
        }
      });
    } catch {
      // reindex failure is non-fatal
    }
  }

  async stop(): Promise<void> {
    this.ready = false;
  }

  private async exec(args: string[]): Promise<{ stdout: string; stderr: string }> {
    const candidates = [
      path.join(this.workspaceRoot, "node_modules", ".bin", "qmd"),
      path.join(process.cwd(), "node_modules", ".bin", "qmd"),
      "qmd",
    ];
    let lastError: unknown = null;
    for (const bin of candidates) {
      try {
        return await execFileAsync(bin, args, {
          cwd: this.workspaceRoot,
          timeout: 30_000,
        });
      } catch (err) {
        lastError = err;
      }
    }
    throw lastError instanceof Error ? lastError : new Error("qmd command failed");
  }

  private async withReadSlot<T>(fn: () => Promise<T>): Promise<T> {
    const release = await this.acquire("read");
    try {
      return await fn();
    } finally {
      release();
    }
  }

  private async withWriteSlot<T>(fn: () => Promise<T>): Promise<T> {
    const release = await this.acquire("write");
    try {
      return await fn();
    } finally {
      release();
    }
  }

  private acquire(kind: "read" | "write"): Promise<() => void> {
    return new Promise((resolve) => {
      const start = (): void => {
        if (kind === "read") {
          this.activeReads += 1;
        } else {
          this.writerActive = true;
        }
        resolve(() => {
          if (kind === "read") {
            this.activeReads = Math.max(0, this.activeReads - 1);
          } else {
            this.writerActive = false;
          }
          this.drainQueue();
        });
      };
      this.queue.push({ kind, start });
      this.drainQueue();
    });
  }

  private drainQueue(): void {
    if (this.writerActive) return;
    while (this.queue.length > 0) {
      const next = this.queue[0];
      if (!next) return;
      if (next.kind === "write") {
        if (this.activeReads > 0) return;
        this.queue.shift();
        next.start();
        return;
      }
      if (this.activeReads >= this.maxReadConcurrency) return;
      this.queue.shift();
      next.start();
    }
  }
}
