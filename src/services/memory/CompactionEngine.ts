/**
 * CompactionEngine — Native hipocampus compaction for core-agent.
 *
 * Port of hipocampus/cli/compact.mjs with a key difference:
 * instead of marking above-threshold nodes as "needs-summarization",
 * we call the bot's main model via LLM to summarize directly.
 *
 * Compaction tree: raw memory/*.md → daily/ → weekly/ → monthly/ → ROOT.md
 */

import { readdir, readFile, writeFile, mkdir, stat } from "node:fs/promises";
import { join } from "node:path";
import type { LLMEvent, LLMStreamRequest } from "../../transport/LLMClient.js";

// ─── Types ───

export interface CompactionConfig {
  cooldownHours: number;  // default 24
  rootMaxTokens: number;  // default 3000
  model: string;          // bot's main model e.g. "claude-opus-4-6"
}

export interface CompactionResult {
  skipped: boolean;
  compacted: boolean;
  stats: {
    daily: string[];
    weekly: string[];
    monthly: string[];
  };
}

export interface CompactionState {
  lastCompactionRun: string | null;
  rawLinesSinceLastCompaction: number;
  checkpointsSinceLastCompaction: number;
}

/** Minimal LLM streaming interface (subset of LLMClient). */
export interface CompactionLLM {
  stream(req: LLMStreamRequest): AsyncIterable<LLMEvent>;
}

// ─── Constants ───

const DAILY_THRESHOLD = 200;
const WEEKLY_THRESHOLD = 300;
const MONTHLY_THRESHOLD = 500;
const SUMMARIZE_TIMEOUT_MS = 120_000;

const DEFAULT_CONFIG: Omit<CompactionConfig, "model"> = {
  cooldownHours: 24,
  rootMaxTokens: 3000,
};

// ─── Secret Scanner ───

const SECRET_PATTERNS = [
  /(?:api[_-]?key|apikey)\s*[:=]\s*['"]?[A-Za-z0-9_\-]{20,}/i,
  /(?:secret|password|passwd|pwd)\s*[:=]\s*['"]?[^\s'"]{8,}/i,
  /(?:token)\s*[:=]\s*['"]?[A-Za-z0-9_\-.]{20,}/i,
  /(?:sk-|pk_live_|pk_test_|ghp_|gho_|github_pat_)[A-Za-z0-9_\-]{20,}/,
  /-----BEGIN (?:RSA |EC )?PRIVATE KEY-----/,
  /(?:Bearer\s+)[A-Za-z0-9_\-.]{20,}/i,
];

const scanLine = (line: string): boolean => SECRET_PATTERNS.some(p => p.test(line));

export const scanSecrets = (content: string): string =>
  content.split("\n").map(l => scanLine(l) ? "[REDACTED: secret detected]" : l).join("\n");

// ─── Date helpers ───

const DATE_RE = /^(\d{4}-\d{2}-\d{2})\.md$/;
const WEEK_RE = /^(\d{4}-W\d{2})\.md$/;
const MONTH_RE = /^(\d{4}-\d{2})\.md$/;

export function isoWeek(dateStr: string): string {
  const d = new Date(dateStr + "T12:00:00Z");
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
}

export function monthOf(dateStr: string): string {
  return dateStr.slice(0, 7);
}

export function daysSince(dateStr: string, today: string): number {
  const then = new Date(dateStr + "T00:00:00Z");
  const now = new Date(today + "T00:00:00Z");
  return Math.floor((now.getTime() - then.getTime()) / 86400000);
}

export function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ─── Helpers ───

async function fileExists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function safeReadFile(path: string): Promise<string> {
  try {
    return await readFile(path, "utf8");
  } catch {
    return "";
  }
}

async function listDir(dir: string): Promise<string[]> {
  try {
    return await readdir(dir);
  } catch {
    return [];
  }
}

function countLines(content: string): number {
  if (!content) return 0;
  return content.split("\n").length;
}

function weekToApproxMonth(week: string): string {
  const [yearStr, weekNumStr] = week.split("-W");
  const year = parseInt(yearStr!, 10);
  const weekNum = parseInt(weekNumStr!, 10);
  const approxDate = new Date(Date.UTC(year, 0, 1 + (weekNum - 1) * 7));
  return `${year}-${String(approxDate.getUTCMonth() + 1).padStart(2, "0")}`;
}

function frontmatterStatus(content: string): string | null {
  const lines = content.split(/\r?\n/);
  if (lines[0] !== "---") return null;

  for (const line of lines.slice(1)) {
    if (line === "---") return null;
    const match = line.match(/^\s*status\s*:\s*["']?([A-Za-z_-]+)["']?\s*$/);
    if (match) return match[1]!;
  }

  return null;
}

function isFixedNode(content: string): boolean {
  return frontmatterStatus(content) === "fixed";
}

// ─── CompactionEngine ───

export class CompactionEngine {
  private readonly memoryDir: string;
  private readonly dailyDir: string;
  private readonly weeklyDir: string;
  private readonly monthlyDir: string;
  private readonly statePath: string;
  private readonly today: string;

  constructor(
    private readonly workspaceRoot: string,
    private readonly config: CompactionConfig,
    private readonly llm: CompactionLLM,
  ) {
    this.memoryDir = join(workspaceRoot, "memory");
    this.dailyDir = join(this.memoryDir, "daily");
    this.weeklyDir = join(this.memoryDir, "weekly");
    this.monthlyDir = join(this.memoryDir, "monthly");
    this.statePath = join(this.memoryDir, ".compaction-state.json");
    const now = new Date();
    this.today = fmtDate(now);
  }

  /** Read hipocampus.config.json if exists, merge with defaults. */
  static async loadConfig(workspaceRoot: string, defaultModel: string): Promise<CompactionConfig> {
    const configPath = join(workspaceRoot, "hipocampus.config.json");
    let fileConfig: Record<string, unknown> = {};
    try {
      const raw = await readFile(configPath, "utf8");
      fileConfig = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      // no config file — use defaults
    }
    const compactionConfig =
      typeof fileConfig["compaction"] === "object" &&
      fileConfig["compaction"] !== null &&
      !Array.isArray(fileConfig["compaction"])
        ? fileConfig["compaction"] as Record<string, unknown>
        : {};
    const readNumber = (key: "cooldownHours" | "rootMaxTokens", fallback: number): number => {
      const topLevel = fileConfig[key];
      if (typeof topLevel === "number") return topLevel;
      const nested = compactionConfig[key];
      if (typeof nested === "number") return nested;
      return fallback;
    };
    return {
      cooldownHours: readNumber("cooldownHours", DEFAULT_CONFIG.cooldownHours),
      rootMaxTokens: readNumber("rootMaxTokens", DEFAULT_CONFIG.rootMaxTokens),
      model: typeof fileConfig["model"] === "string"
        ? fileConfig["model"] : defaultModel,
    };
  }

  /** Main entry point. */
  async run(force?: boolean): Promise<CompactionResult> {
    const result: CompactionResult = {
      skipped: false,
      compacted: false,
      stats: { daily: [], weekly: [], monthly: [] },
    };

    // Ensure directories exist
    await mkdir(this.dailyDir, { recursive: true });
    await mkdir(this.weeklyDir, { recursive: true });
    await mkdir(this.monthlyDir, { recursive: true });

    // Cooldown check
    if (!force) {
      const state = await this.loadState();
      if (state.lastCompactionRun) {
        const hoursSince = (Date.now() - new Date(state.lastCompactionRun).getTime()) / 3600000;
        if (hoursSince < this.config.cooldownHours) {
          result.skipped = true;
          return result;
        }
      }
    }

    // Compact each tier independently — existing files at any tier
    // should always be processed even if higher tiers had nothing new.
    const dailyUpdated = await this.compactDaily(result);
    const weeklyUpdated = await this.compactWeekly(result);
    const monthlyUpdated = await this.compactMonthly(result);

    // Always regenerate ROOT.md when any monthly content exists
    if (dailyUpdated || weeklyUpdated || monthlyUpdated) {
      await this.compactRoot();
    } else {
      // Even with no updates, regenerate root if monthly files exist
      // but root doesn't (e.g. first run after partial compaction)
      await this.compactRootIfNeeded();
    }

    result.compacted = dailyUpdated || weeklyUpdated || monthlyUpdated;

    // Update state
    if (result.compacted) {
      const state: CompactionState = {
        lastCompactionRun: new Date().toISOString(),
        rawLinesSinceLastCompaction: 0,
        checkpointsSinceLastCompaction: 0,
      };
      await this.saveState(state);
    }

    return result;
  }

  // ─── Daily: raw memory/*.md → memory/daily/*.md ───

  private async compactDaily(result: CompactionResult): Promise<boolean> {
    const files = await listDir(this.memoryDir);
    const rawDates = files
      .filter(f => DATE_RE.test(f))
      .map(f => f.match(DATE_RE)![1]!)
      .sort();

    let updated = false;

    for (const date of rawDates) {
      const rawPath = join(this.memoryDir, `${date}.md`);
      const dailyPath = join(this.dailyDir, `${date}.md`);
      const isToday = date === this.today;
      const status = isToday ? "tentative" : "fixed";

      // Skip if daily node exists and is fixed
      if (await fileExists(dailyPath)) {
        const existing = await safeReadFile(dailyPath);
        if (isFixedNode(existing)) continue;
      }

      const rawContent = await safeReadFile(rawPath);
      const rawLines = countLines(rawContent);
      if (rawLines === 0) continue;

      const safeContent = scanSecrets(rawContent);
      if (rawLines <= DAILY_THRESHOLD) {
        // Below threshold — copy verbatim with secret redaction
        const frontmatter = `---\ntype: daily\nstatus: ${status}\nperiod: ${date}\nsource-files: [memory/${date}.md]\ntopics: []\n---\n\n`;
        await writeFile(dailyPath, frontmatter + safeContent);
        updated = true;
        result.stats.daily.push(date);
      } else {
        // Above threshold — LLM summarize with fallback to raw content
        const summary = await this.safeSummarize(
          rawContent,
          `Summarize this daily log for ${date}. Preserve key decisions, actions taken, issues encountered, and important context. Be concise but complete. Output markdown.`,
          safeContent,
        );
        const frontmatter = `---\ntype: daily\nstatus: ${status}\nperiod: ${date}\nsource-files: [memory/${date}.md]\nlines: ${rawLines}\ntopics: []\n---\n\n`;
        await writeFile(dailyPath, frontmatter + summary);
        updated = true;
        result.stats.daily.push(date);
      }
    }

    return updated;
  }

  // ─── Weekly: daily/ → weekly/ ───

  private async compactWeekly(result: CompactionResult): Promise<boolean> {
    const files = await listDir(this.dailyDir);
    const dailyDates = files
      .filter(f => DATE_RE.test(f))
      .map(f => f.match(DATE_RE)![1]!)
      .sort();

    // Group by ISO week
    const weekGroups = new Map<string, string[]>();
    for (const date of dailyDates) {
      const week = isoWeek(date);
      if (!weekGroups.has(week)) weekGroups.set(week, []);
      weekGroups.get(week)!.push(date);
    }

    let updated = false;

    for (const [week, dates] of weekGroups) {
      const weeklyPath = join(this.weeklyDir, `${week}.md`);

      // Status: fixed if all dates past and oldest >= 7 days ago
      const allPast = dates.every(d => d < this.today);
      const oldestDate = dates[0]!;
      const isFixed = allPast && daysSince(oldestDate, this.today) >= 7;
      const status = isFixed ? "fixed" : "tentative";

      // Skip if already fixed
      if (await fileExists(weeklyPath)) {
        const existing = await safeReadFile(weeklyPath);
        if (isFixedNode(existing)) continue;
      }

      // Combine daily contents
      let combined = "";
      let totalLines = 0;
      for (const date of dates) {
        const dailyPath = join(this.dailyDir, `${date}.md`);
        const content = await safeReadFile(dailyPath);
        if (!content || content.includes("needs-summarization")) continue;
        combined += `\n\n# ${date}\n\n` + content;
        totalLines += countLines(content);
      }

      if (totalLines === 0) continue;

      if (totalLines <= WEEKLY_THRESHOLD) {
        const frontmatter = `---\ntype: weekly\nstatus: ${status}\nperiod: ${week}\ndates: ${dates[0]} to ${dates[dates.length - 1]}\nsource-files: [${dates.map(d => `memory/daily/${d}.md`).join(", ")}]\ntopics: []\n---\n`;
        await writeFile(weeklyPath, frontmatter + combined);
        updated = true;
        result.stats.weekly.push(week);
      } else {
        // Above threshold — LLM summarize with fallback
        const summary = await this.safeSummarize(
          combined,
          `Summarize this weekly log for ${week} (${dates[0]} to ${dates[dates.length - 1]}). Preserve key decisions, patterns, blockers, and important context. Be concise but complete. Output markdown.`,
          combined,
        );
        const frontmatter = `---\ntype: weekly\nstatus: ${status}\nperiod: ${week}\ndates: ${dates[0]} to ${dates[dates.length - 1]}\nsource-files: [${dates.map(d => `memory/daily/${d}.md`).join(", ")}]\nlines: ${totalLines}\ntopics: []\n---\n`;
        await writeFile(weeklyPath, frontmatter + summary);
        updated = true;
        result.stats.weekly.push(week);
      }
    }

    return updated;
  }

  // ─── Monthly: weekly/ → monthly/ ───

  private async compactMonthly(result: CompactionResult): Promise<boolean> {
    const files = await listDir(this.weeklyDir);
    const weeklyNames = files
      .filter(f => WEEK_RE.test(f))
      .map(f => f.match(WEEK_RE)![1]!)
      .sort();

    // Group by month
    const monthGroups = new Map<string, string[]>();
    for (const week of weeklyNames) {
      const month = weekToApproxMonth(week);
      if (!monthGroups.has(month)) monthGroups.set(month, []);
      monthGroups.get(month)!.push(week);
    }

    let updated = false;

    for (const [month, weeks] of monthGroups) {
      const monthlyPath = join(this.monthlyDir, `${month}.md`);

      // Status: fixed if month ended + 7 days
      const monthEnd = new Date(Date.UTC(
        parseInt(month.slice(0, 4)),
        parseInt(month.slice(5)),
        0,
      ));
      const monthEndStr = monthEnd.toISOString().slice(0, 10);
      const isFixed = daysSince(monthEndStr, this.today) >= 7;
      const status = isFixed ? "fixed" : "tentative";

      // Skip if already fixed
      if (await fileExists(monthlyPath)) {
        const existing = await safeReadFile(monthlyPath);
        if (isFixedNode(existing)) continue;
      }

      // Combine weekly contents
      let combined = "";
      let totalLines = 0;
      for (const week of weeks) {
        const weeklyPath = join(this.weeklyDir, `${week}.md`);
        const content = await safeReadFile(weeklyPath);
        if (!content || content.includes("needs-summarization")) continue;
        combined += `\n\n# ${week}\n\n` + content;
        totalLines += countLines(content);
      }

      if (totalLines === 0) continue;

      if (totalLines <= MONTHLY_THRESHOLD) {
        const frontmatter = `---\ntype: monthly\nstatus: ${status}\nperiod: ${month}\nweeks: [${weeks.join(", ")}]\nsource-files: [${weeks.map(w => `memory/weekly/${w}.md`).join(", ")}]\ntopics: []\n---\n`;
        await writeFile(monthlyPath, frontmatter + combined);
        updated = true;
        result.stats.monthly.push(month);
      } else {
        // Above threshold — LLM summarize with fallback
        const summary = await this.safeSummarize(
          combined,
          `Summarize this monthly log for ${month}. Preserve key themes, decisions, patterns, and important context. Be concise but complete. Output markdown.`,
          combined,
        );
        const frontmatter = `---\ntype: monthly\nstatus: ${status}\nperiod: ${month}\nweeks: [${weeks.join(", ")}]\nsource-files: [${weeks.map(w => `memory/weekly/${w}.md`).join(", ")}]\nlines: ${totalLines}\ntopics: []\n---\n`;
        await writeFile(monthlyPath, frontmatter + summary);
        updated = true;
        result.stats.monthly.push(month);
      }
    }

    return updated;
  }

  // ─── Root: monthly/ → ROOT.md ───

  /** Regenerate ROOT.md only if monthly content exists but ROOT.md doesn't. */
  private async compactRootIfNeeded(): Promise<void> {
    const rootPath = join(this.memoryDir, "ROOT.md");
    if (await fileExists(rootPath)) return;
    const files = await listDir(this.monthlyDir);
    if (files.some(f => MONTH_RE.test(f))) {
      await this.compactRoot();
    }
  }

  private async compactRoot(): Promise<void> {
    const rootPath = join(this.memoryDir, "ROOT.md");
    const files = await listDir(this.monthlyDir);
    const monthlyNames = files
      .filter(f => MONTH_RE.test(f))
      .sort();

    if (monthlyNames.length === 0) return;

    // Gather all monthly content
    let allContent = "";
    for (const name of monthlyNames) {
      const content = await safeReadFile(join(this.monthlyDir, name));
      if (content) allContent += `\n\n# ${name.replace(".md", "")}\n\n` + content;
    }

    if (!allContent.trim()) return;

    // Generate root summary via LLM (fallback: concatenated monthly content)
    const rootSummary = await this.safeSummarize(
      allContent,
      [
        `Generate a ROOT.md memory index with these sections:`,
        `## Active Context (recent ~7 days) — bullet points of recent work`,
        `## Recent Patterns — recurring themes and lessons learned`,
        `## Historical Summary — month-by-month summary`,
        `## Topics Index — categorized topic tags`,
        `Cap total output at approximately ${this.config.rootMaxTokens} tokens.`,
        `Output markdown.`,
      ].join("\n"),
      allContent,
    );

    await writeFile(rootPath, rootSummary);
  }

  // ─── LLM Summarization ───

  /**
   * Try LLM summarization; on any error (auth, timeout, rate-limit),
   * fall back to the provided raw content so compaction never crashes.
   */
  private async safeSummarize(content: string, instruction: string, fallback: string): Promise<string> {
    try {
      return await this.summarize(content, instruction);
    } catch (err) {
      console.warn(`[compaction] LLM summarize failed, using raw fallback: ${(err as Error).message}`);
      return fallback;
    }
  }

  private async summarize(content: string, instruction: string): Promise<string> {
    const chunks: string[] = [];

    const timeoutPromise = new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error("summarize timeout")), SUMMARIZE_TIMEOUT_MS);
    });

    const streamPromise = (async () => {
      const stream = this.llm.stream({
        model: this.config.model,
        system: "You are a memory compaction assistant. Summarize the provided content accurately and concisely.",
        messages: [{ role: "user", content: `${instruction}\n\n---\n\n${content}` }],
        max_tokens: 4096,
        temperature: 0,
        thinking: { type: "disabled" },
      });
      for await (const event of stream) {
        if (event.kind === "text_delta" && "delta" in event && event.delta) {
          chunks.push(event.delta);
        }
        if (event.kind === "message_end") break;
      }
    })();

    await Promise.race([streamPromise, timeoutPromise]);

    return chunks.join("");
  }

  // ─── State persistence ───

  private async loadState(): Promise<CompactionState> {
    const defaults: CompactionState = {
      lastCompactionRun: null,
      rawLinesSinceLastCompaction: 0,
      checkpointsSinceLastCompaction: 0,
    };
    try {
      const raw = await readFile(this.statePath, "utf8");
      const parsed = JSON.parse(raw) as Partial<CompactionState>;
      return { ...defaults, ...parsed };
    } catch {
      return defaults;
    }
  }

  private async saveState(state: CompactionState): Promise<void> {
    try {
      await writeFile(this.statePath, JSON.stringify(state, null, 2));
    } catch {
      // state write is best-effort
    }
  }
}
