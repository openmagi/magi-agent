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
        await this.exec(["embed"]);
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

  async reindex(): Promise<void> {
    if (!this.ready) return;
    try {
      await this.exec(["update"]);
      if (this.vectorEnabled) {
        await this.exec(["embed"]);
      }
    } catch {
      // reindex failure is non-fatal
    }
  }

  async stop(): Promise<void> {
    this.ready = false;
  }

  private async exec(args: string[]): Promise<{ stdout: string; stderr: string }> {
    // Try local node_modules first, then global
    const localBin = path.join(this.workspaceRoot, "node_modules", ".bin", "qmd");
    try {
      return await execFileAsync(localBin, args, {
        cwd: this.workspaceRoot,
        timeout: 30_000,
      });
    } catch {
      // Fall back to global qmd
      return execFileAsync("qmd", args, {
        cwd: this.workspaceRoot,
        timeout: 30_000,
      });
    }
  }
}
