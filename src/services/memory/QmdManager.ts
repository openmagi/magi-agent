import { execFile as execFileCb } from "node:child_process";
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

  constructor(
    private readonly workspaceRoot: string,
    private readonly vectorEnabled: boolean,
  ) {
    this.memoryDir = path.join(workspaceRoot, "memory");
  }

  isReady(): boolean {
    return this.ready;
  }

  async start(): Promise<void> {
    try {
      // Register memory collection (silent fail if exists)
      await this.exec(["collection", "add", this.memoryDir, "--name", "memory"]).catch(() => {});
      // Initial index build
      await this.exec(["update"]);
      if (this.vectorEnabled) {
        await this.exec(["embed"]).catch(() => {});
      }
      this.ready = true;
    } catch {
      this.ready = false;
    }
  }

  async search(query: string, opts?: QmdSearchOpts): Promise<QmdSearchResult[]> {
    if (!this.ready) return [];
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
  }

  async vectorSearch(query: string, opts?: QmdSearchOpts): Promise<QmdSearchResult[]> {
    if (!this.ready || !this.vectorEnabled) return [];
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
      await this.exec(["update"]);
      if (this.vectorEnabled) {
        await this.exec(["embed"]).catch(() => {});
      }
    } catch {
      // reindex failure is non-fatal
    }
  }

  async stop(): Promise<void> {
    this.ready = false;
  }

  private async exec(args: string[]): Promise<{ stdout: string; stderr: string }> {
    const qmdBinaries = [
      path.join(this.workspaceRoot, "node_modules", ".bin", "qmd"),
      "/app/node_modules/.bin/qmd",
      "qmd",
    ];

    let lastError: unknown;
    for (const bin of qmdBinaries) {
      try {
        return await execFileAsync(bin, args, {
          cwd: this.workspaceRoot,
          timeout: 30_000,
        });
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError instanceof Error ? lastError : new Error("qmd unavailable");
  }
}
