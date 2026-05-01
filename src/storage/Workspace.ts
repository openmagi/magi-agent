/**
 * Workspace — reads the bot's legacy-compatible workspace directory.
 * Design reference: §9.5.
 *
 * Core-agent mounts `/home/ocuser/.clawy/workspace` — the same PVC
 * shape existing legacy gateway bots have. We read identity/memory files for
 * the layered context on each turn; writes happen through
 * StagedWriteJournal (Phase 1c).
 */

import fs from "node:fs/promises";
import path from "node:path";

export interface WorkspaceHarnessRuleFile {
  path: string;
  content: string;
}

export interface WorkspaceIdentity {
  /** BOOTSTRAP.md — short identity prologue rendered first in system. */
  bootstrap?: string;
  /** SOUL.md — main agent spec / persona / rules. */
  soul?: string;
  /** LEARNING.md — platform-owned learning/procedural-memory contract. */
  learning?: string;
  /** IDENTITY.md — extended bio / role definition. */
  identity?: string;
  /** USER.md — user profile (preferences, role, language). */
  user?: string;
  /** AGENTS.md — sub-persona registry (parsed later, not yet used). */
  agents?: string;
  /** TOOLS.md — documentation only, not enforcement. */
  tools?: string;
  /**
   * USER-RULES.md — user-defined Agent Rules from the dashboard.
   * Persisted to `bots.agent_rules` (migration 079) and written into the
   * workspace by the provisioning-worker init container. Capped at 5000
   * chars by the DB layer; the reader applies the same cap defensively.
   */
  userRules?: string;
  /**
   * Optional user-installed harness rule Markdown files. OSS users can
   * place a single `USER-HARNESS-RULES.md` file at the workspace root
   * or drop downloaded packs into `harness-rules/*.md`.
   */
  userHarnessRules?: WorkspaceHarnessRuleFile[];
}

/**
 * Hard cap for `userRules` content (chars). Must match the DB/FE cap
 * (`src/lib/validation/schemas.ts` — agent_rules.max(5000)). Applied here
 * defensively in case the file on disk has been tampered with.
 */
export const USER_RULES_MAX_CHARS = 5000;
export const USER_HARNESS_RULES_MAX_CHARS = 20000;

export interface WorkspaceMemory {
  /** `memory/ROOT.md` preferred, fallback `MEMORY.md` for legacy bots. */
  rootIndex?: string;
}

export class Workspace {
  constructor(readonly root: string) {}

  async exists(): Promise<boolean> {
    try {
      const st = await fs.stat(this.root);
      return st.isDirectory();
    } catch {
      return false;
    }
  }

  private async readSafe(relPath: string): Promise<string | undefined> {
    try {
      const full = path.join(this.root, relPath);
      const txt = await fs.readFile(full, "utf8");
      return txt;
    } catch {
      return undefined;
    }
  }

  private async listHarnessRuleFiles(): Promise<WorkspaceHarnessRuleFile[]> {
    const files: WorkspaceHarnessRuleFile[] = [];
    const add = (relPath: string, raw: string | undefined): void => {
      if (!raw || raw.trim().length === 0) return;
      const content =
        raw.length > USER_HARNESS_RULES_MAX_CHARS
          ? `${raw.slice(0, USER_HARNESS_RULES_MAX_CHARS)}\n[truncated]`
          : raw;
      files.push({ path: relPath, content });
    };

    add("USER-HARNESS-RULES.md", await this.readSafe("USER-HARNESS-RULES.md"));

    try {
      const entries = await fs.readdir(path.join(this.root, "harness-rules"), {
        withFileTypes: true,
      });
      for (const entry of entries
        .filter((item) => item.isFile() && item.name.endsWith(".md"))
        .sort((a, b) => a.name.localeCompare(b.name))) {
        const relPath = path.join("harness-rules", entry.name);
        add(relPath, await this.readSafe(relPath));
      }
    } catch {
      // Optional directory; absence is normal.
    }

    return files;
  }

  async loadIdentity(): Promise<WorkspaceIdentity> {
    const [bootstrap, soul, learning, identity, user, agents, tools, userRulesRaw, userHarnessRules] =
      await Promise.all([
        this.readSafe("BOOTSTRAP.md"),
        this.readSafe("SOUL.md"),
        this.readSafe("LEARNING.md"),
        this.readSafe("IDENTITY.md"),
        this.readSafe("USER.md"),
        this.readSafe("AGENTS.md"),
        this.readSafe("TOOLS.md"),
        this.readSafe("USER-RULES.md"),
        this.listHarnessRuleFiles(),
      ]);
    // Defensive cap — the DB/FE already caps at 5000 chars, but if a
    // bot's PVC is tampered with (or the file is appended to by a skill
    // over time) we truncate here so a malicious/oversized file can't
    // balloon the system prompt. Truncation marker helps the model know
    // content was cut.
    let userRules: string | undefined;
    if (typeof userRulesRaw === "string" && userRulesRaw.trim().length > 0) {
      userRules =
        userRulesRaw.length > USER_RULES_MAX_CHARS
          ? `${userRulesRaw.slice(0, USER_RULES_MAX_CHARS)}\n[truncated]`
          : userRulesRaw;
    }
    return {
      bootstrap,
      soul,
      learning,
      identity,
      user,
      agents,
      tools,
      userRules,
      userHarnessRules: userHarnessRules.length > 0 ? userHarnessRules : undefined,
    };
  }

  async loadMemoryIndex(): Promise<WorkspaceMemory> {
    const rootIndex =
      (await this.readSafe("memory/ROOT.md")) ??
      (await this.readSafe("MEMORY.md"));
    return { rootIndex };
  }

  /**
   * Resolve a path inside the workspace. Throws if the resolved path
   * escapes the workspace root (path traversal defence — tools use
   * this helper before any read/write).
   */
  resolve(relPath: string): string {
    const normalised = path.normalize(relPath).replace(/^\/+/, "");
    const full = path.join(this.root, normalised);
    const root = path.resolve(this.root);
    const resolved = path.resolve(full);
    if (!resolved.startsWith(root + path.sep) && resolved !== root) {
      throw new Error(`path escapes workspace: ${relPath}`);
    }
    return resolved;
  }

  /** Read a workspace-relative file as utf8 text (scope-checked). */
  async readFile(relPath: string): Promise<string> {
    const resolved = this.resolve(relPath);
    return fs.readFile(resolved, "utf8");
  }

  /**
   * Write a workspace-relative file (scope-checked). Parent dirs are
   * created as needed.
   */
  async writeFile(relPath: string, content: string): Promise<void> {
    const resolved = this.resolve(relPath);
    await fs.mkdir(path.dirname(resolved), { recursive: true });
    await fs.writeFile(resolved, content, "utf8");
  }
}

/**
 * Render the identity block into a single system-prompt string.
 *
 * Order (§9.5 + SOUL.md convention):
 *   BOOTSTRAP → SOUL → LEARNING → IDENTITY → USER → AGENTS → TOOLS
 *
 * Missing files are omitted, not replaced with "<missing>". Each file
 * is wrapped in a clear section header so the model can distinguish.
 */
export function renderIdentitySystem(id: WorkspaceIdentity): string {
  const parts: string[] = [];
  const push = (label: string, body: string | undefined): void => {
    if (!body || body.trim().length === 0) return;
    parts.push(`# ${label}\n\n${body.trim()}`);
  };
  push("BOOTSTRAP", id.bootstrap);
  push("SOUL", id.soul);
  push("LEARNING", id.learning);
  push("IDENTITY", id.identity);
  push("USER", id.user);
  push("AGENTS", id.agents);
  push("TOOLS", id.tools);
  return parts.join("\n\n---\n\n");
}
