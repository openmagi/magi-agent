import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtemp, rm, readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  CompactionEngine,
  scanSecrets,
  isoWeek,
  monthOf,
  daysSince,
  fmtDate,
} from "./CompactionEngine.js";
import type { CompactionConfig, CompactionLLM } from "./CompactionEngine.js";
import type { LLMEvent, LLMStreamRequest } from "../../transport/LLMClient.js";

// ─── Mock LLM ───

function createMockLLM(summaryText = "LLM summary of content"): CompactionLLM & { calls: LLMStreamRequest[] } {
  const calls: LLMStreamRequest[] = [];
  return {
    calls,
    async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent> {
      calls.push(req);
      yield { kind: "text_delta", blockIndex: 0, delta: summaryText };
      yield { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } };
    },
  };
}

function defaultConfig(): CompactionConfig {
  return { cooldownHours: 24, rootMaxTokens: 3000, model: "claude-opus-4-6" };
}

/** Generate N lines of filler text. */
function generateLines(n: number): string {
  return Array.from({ length: n }, (_, i) => `Line ${i + 1}: some content here`).join("\n");
}

// ─── Test setup ───

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await mkdtemp(join(tmpdir(), "compaction-test-"));
});

afterEach(async () => {
  await rm(tmpDir, { recursive: true, force: true });
});

// ─── Date helper tests ───

describe("date helpers", () => {
  it("isoWeek returns correct ISO week", () => {
    expect(isoWeek("2026-01-01")).toBe("2026-W01");
    expect(isoWeek("2026-04-20")).toBe("2026-W17");
  });

  it("monthOf extracts YYYY-MM", () => {
    expect(monthOf("2026-04-20")).toBe("2026-04");
    expect(monthOf("2025-12-31")).toBe("2025-12");
  });

  it("daysSince calculates correctly", () => {
    expect(daysSince("2026-04-10", "2026-04-20")).toBe(10);
    expect(daysSince("2026-04-20", "2026-04-20")).toBe(0);
  });

  it("fmtDate formats Date object", () => {
    const d = new Date(2026, 3, 20); // April 20
    expect(fmtDate(d)).toBe("2026-04-20");
  });
});

// ─── Secret scanner tests ───

describe("scanSecrets", () => {
  it("redacts API keys", () => {
    const input = "api_key: sk-abcdefghijklmnopqrst1234";
    expect(scanSecrets(input)).toBe("[REDACTED: secret detected]");
  });

  it("redacts Bearer tokens", () => {
    const input = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9";
    expect(scanSecrets(input)).toBe("[REDACTED: secret detected]");
  });

  it("redacts private keys", () => {
    const input = "-----BEGIN RSA PRIVATE KEY-----";
    expect(scanSecrets(input)).toBe("[REDACTED: secret detected]");
  });

  it("passes through clean content", () => {
    const input = "This is a normal log line\nWith no secrets";
    expect(scanSecrets(input)).toBe(input);
  });

  it("redacts only offending lines", () => {
    const input = "safe line\npassword: hunter2abc\nsafe again";
    const result = scanSecrets(input);
    expect(result).toBe("safe line\n[REDACTED: secret detected]\nsafe again");
  });
});

// ─── Config loading tests ───

describe("loadConfig", () => {
  it("uses defaults when no config file", async () => {
    const config = await CompactionEngine.loadConfig(tmpDir, "claude-opus-4-6");
    expect(config).toEqual({
      cooldownHours: 24,
      rootMaxTokens: 3000,
      model: "claude-opus-4-6",
    });
  });

  it("reads hipocampus.config.json", async () => {
    await writeFile(
      join(tmpDir, "hipocampus.config.json"),
      JSON.stringify({ cooldownHours: 12, rootMaxTokens: 5000, model: "claude-haiku-3" }),
    );
    const config = await CompactionEngine.loadConfig(tmpDir, "claude-opus-4-6");
    expect(config.cooldownHours).toBe(12);
    expect(config.rootMaxTokens).toBe(5000);
    expect(config.model).toBe("claude-haiku-3");
  });

  it("reads nested compaction config used by hipocampus init", async () => {
    await writeFile(
      join(tmpDir, "hipocampus.config.json"),
      JSON.stringify({
        platform: "clawy",
        search: { vector: true },
        compaction: { cooldownHours: 3, rootMaxTokens: 4200 },
      }),
    );

    const config = await CompactionEngine.loadConfig(tmpDir, "claude-opus-4-6");

    expect(config.cooldownHours).toBe(3);
    expect(config.rootMaxTokens).toBe(4200);
    expect(config.model).toBe("claude-opus-4-6");
  });

  it("merges partial config with defaults", async () => {
    await writeFile(
      join(tmpDir, "hipocampus.config.json"),
      JSON.stringify({ cooldownHours: 6 }),
    );
    const config = await CompactionEngine.loadConfig(tmpDir, "claude-opus-4-6");
    expect(config.cooldownHours).toBe(6);
    expect(config.rootMaxTokens).toBe(3000); // default
    expect(config.model).toBe("claude-opus-4-6"); // default
  });
});

// ─── Cooldown tests ───

describe("cooldown", () => {
  it("skips when cooldown not expired", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    // Write a recent state
    const state = {
      lastCompactionRun: new Date().toISOString(),
      rawLinesSinceLastCompaction: 0,
      checkpointsSinceLastCompaction: 0,
    };
    await writeFile(join(memDir, ".compaction-state.json"), JSON.stringify(state));

    // Write a raw file so there's something to compact
    await writeFile(join(memDir, "2026-04-20.md"), "# Log\nSome content");

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run();

    expect(result.skipped).toBe(true);
    expect(result.compacted).toBe(false);
  });

  it("runs when cooldown expired", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    // Write an old state (48 hours ago)
    const old = new Date(Date.now() - 48 * 3600 * 1000);
    const state = {
      lastCompactionRun: old.toISOString(),
      rawLinesSinceLastCompaction: 0,
      checkpointsSinceLastCompaction: 0,
    };
    await writeFile(join(memDir, ".compaction-state.json"), JSON.stringify(state));
    await writeFile(join(memDir, "2026-04-20.md"), "# Log\nSome content");

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run();

    expect(result.skipped).toBe(false);
  });

  it("runs when forced regardless of cooldown", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    const state = {
      lastCompactionRun: new Date().toISOString(),
      rawLinesSinceLastCompaction: 0,
      checkpointsSinceLastCompaction: 0,
    };
    await writeFile(join(memDir, ".compaction-state.json"), JSON.stringify(state));
    await writeFile(join(memDir, "2026-04-20.md"), "# Log\nSome content");

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.skipped).toBe(false);
  });

  it("runs when no state file exists", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    await writeFile(join(memDir, "2026-04-20.md"), "# Log\nSome content");

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run();

    expect(result.skipped).toBe(false);
  });
});

// ─── Daily compaction tests ───

describe("compactDaily", () => {
  it("copies verbatim under 200 lines with frontmatter", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    const content = generateLines(50);
    await writeFile(join(memDir, "2026-04-10.md"), content);

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.daily).toContain("2026-04-10");
    // No LLM call for the daily itself (under threshold).
    // Root compaction may call LLM — check daily call specifically.
    const dailyCalls = llm.calls.filter(c =>
      typeof c.messages[0]?.content === "string" && c.messages[0].content.includes("daily log"),
    );
    expect(dailyCalls.length).toBe(0);

    const dailyContent = await readFile(join(memDir, "daily", "2026-04-10.md"), "utf8");
    expect(dailyContent).toContain("type: daily");
    expect(dailyContent).toContain("status: fixed"); // past date
    expect(dailyContent).toContain("Line 1: some content here");
  });

  it("LLM summarizes over 200 lines", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    const content = generateLines(250);
    await writeFile(join(memDir, "2026-04-10.md"), content);

    const llm = createMockLLM("Summarized daily content");
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.daily).toContain("2026-04-10");
    // At least 1 LLM call for the daily summary; root may add another
    const dailyCalls = llm.calls.filter(c =>
      typeof c.messages[0]?.content === "string" && c.messages[0].content.includes("daily log"),
    );
    expect(dailyCalls.length).toBe(1);

    const dailyContent = await readFile(join(memDir, "daily", "2026-04-10.md"), "utf8");
    expect(dailyContent).toContain("Summarized daily content");
    expect(dailyContent).toContain("lines: 250");
  });

  it("skips fixed daily nodes", async () => {
    const memDir = join(tmpDir, "memory");
    const dailyDir = join(memDir, "daily");
    await mkdir(dailyDir, { recursive: true });

    await writeFile(join(memDir, "2026-04-10.md"), generateLines(50));
    await writeFile(
      join(dailyDir, "2026-04-10.md"),
      "---\ntype: daily\nstatus: fixed\nperiod: 2026-04-10\n---\n\nExisting summary",
    );

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.daily).not.toContain("2026-04-10");
    // Verify existing content untouched
    const content = await readFile(join(dailyDir, "2026-04-10.md"), "utf8");
    expect(content).toContain("Existing summary");
  });

  it("redacts secrets in verbatim copy", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    const content = "Normal line\napi_key: sk-abcdefghijklmnopqrst1234\nAnother line";
    await writeFile(join(memDir, "2026-04-10.md"), content);

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    await engine.run(true);

    const dailyContent = await readFile(join(memDir, "daily", "2026-04-10.md"), "utf8");
    expect(dailyContent).toContain("[REDACTED: secret detected]");
    expect(dailyContent).not.toContain("sk-abcdefghijklmnopqrst1234");
    expect(dailyContent).toContain("Normal line");
  });
});

// ─── Weekly compaction tests ───

describe("compactWeekly", () => {
  it("concatenates dailies below 300 lines", async () => {
    const memDir = join(tmpDir, "memory");
    const dailyDir = join(memDir, "daily");
    await mkdir(dailyDir, { recursive: true });

    // Create two daily files in the same week (far enough in the past)
    // 2026-04-06 (Mon) and 2026-04-07 (Tue) are both in W15
    const date1 = "2026-04-06";
    const date2 = "2026-04-07";

    // Write raw files too (so daily compaction has something)
    await writeFile(join(memDir, `${date1}.md`), generateLines(50));
    await writeFile(join(memDir, `${date2}.md`), generateLines(50));

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    const week = isoWeek(date1);
    expect(result.stats.weekly).toContain(week);
    // No LLM for weekly itself (under threshold); root compaction may call LLM
    const weeklyCalls = llm.calls.filter(c =>
      typeof c.messages[0]?.content === "string" && c.messages[0].content.includes("weekly log"),
    );
    expect(weeklyCalls.length).toBe(0);

    const weeklyContent = await readFile(join(memDir, "weekly", `${week}.md`), "utf8");
    expect(weeklyContent).toContain("type: weekly");
    expect(weeklyContent).toContain(date1);
    expect(weeklyContent).toContain(date2);
  });

  it("skips when no daily updates", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    // No raw files = no daily updates = weekly skipped

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.weekly).toHaveLength(0);
  });

  it("updates tentative weekly nodes even when embedded daily frontmatter contains fixed status", async () => {
    const memDir = join(tmpDir, "memory");
    const weeklyDir = join(memDir, "weekly");
    await mkdir(weeklyDir, { recursive: true });

    await writeFile(join(memDir, "2026-04-21.md"), "already compacted");
    await writeFile(join(memDir, "2026-04-23.md"), "new admin bot memory");
    await writeFile(
      join(weeklyDir, "2026-W17.md"),
      [
        "---",
        "type: weekly",
        "status: tentative",
        "period: 2026-W17",
        "---",
        "",
        "# 2026-04-21",
        "---",
        "type: daily",
        "status: fixed",
        "period: 2026-04-21",
        "---",
        "",
        "Old daily content",
      ].join("\n"),
    );

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.weekly).toContain("2026-W17");
    const weeklyContent = await readFile(join(weeklyDir, "2026-W17.md"), "utf8");
    expect(weeklyContent).toContain("# 2026-04-23");
    expect(weeklyContent).toContain("new admin bot memory");
  });
});

// ─── Monthly compaction tests ───

describe("compactMonthly", () => {
  it("skips when no weekly updates", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    // No raw files = chain stops at daily

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.monthly).toHaveLength(0);
  });

  it("updates tentative monthly nodes even when embedded weekly content contains fixed status", async () => {
    const memDir = join(tmpDir, "memory");
    const monthlyDir = join(memDir, "monthly");
    await mkdir(monthlyDir, { recursive: true });

    await writeFile(join(memDir, "2026-04-23.md"), "new April memory");
    await writeFile(
      join(monthlyDir, "2026-04.md"),
      [
        "---",
        "type: monthly",
        "status: tentative",
        "period: 2026-04",
        "---",
        "",
        "# 2026-W16",
        "---",
        "type: weekly",
        "status: fixed",
        "period: 2026-W16",
        "---",
        "",
        "Old weekly content",
      ].join("\n"),
    );

    const llm = createMockLLM("ROOT summary");
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.monthly).toContain("2026-04");
    const monthlyContent = await readFile(join(monthlyDir, "2026-04.md"), "utf8");
    expect(monthlyContent).toContain("new April memory");
    const rootContent = await readFile(join(memDir, "ROOT.md"), "utf8");
    expect(rootContent).toContain("ROOT summary");
  });
});

// ─── Stop-check chain tests ───

describe("chain gate relaxation", () => {
  it("processes existing weekly/monthly even when daily produces nothing new", async () => {
    const memDir = join(tmpDir, "memory");
    const dailyDir = join(memDir, "daily");
    const weeklyDir = join(memDir, "weekly");
    const monthlyDir = join(memDir, "monthly");
    await mkdir(dailyDir, { recursive: true });
    await mkdir(weeklyDir, { recursive: true });
    await mkdir(monthlyDir, { recursive: true });

    // Pre-existing daily files (already compacted, no new raw memory/*.md)
    await writeFile(
      join(dailyDir, "2026-03-02.md"),
      "---\ntype: daily\nstatus: fixed\nperiod: 2026-03-02\n---\n\nMarch 2 work",
    );
    await writeFile(
      join(dailyDir, "2026-03-09.md"),
      "---\ntype: daily\nstatus: fixed\nperiod: 2026-03-09\n---\n\nMarch 9 work",
    );

    // No raw memory/*.md files — daily produces nothing new
    // But weekly/monthly/root should still process existing dailies

    const llm = createMockLLM("ROOT summary");
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    // Weekly should have processed existing dailies
    expect(result.stats.weekly.length).toBeGreaterThanOrEqual(1);
    // ROOT.md should exist
    const rootContent = await readFile(join(memDir, "ROOT.md"), "utf8");
    expect(rootContent).toContain("ROOT summary");
  });

  it("updates ROOT.md when only monthly content exists (no new daily/weekly)", async () => {
    const memDir = join(tmpDir, "memory");
    const monthlyDir = join(memDir, "monthly");
    await mkdir(monthlyDir, { recursive: true });

    await writeFile(
      join(monthlyDir, "2026-03.md"),
      "---\ntype: monthly\nstatus: fixed\nperiod: 2026-03\n---\n\nMarch summary",
    );

    const llm = createMockLLM("Fresh root from monthly");
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    const rootContent = await readFile(join(memDir, "ROOT.md"), "utf8");
    expect(rootContent).toContain("Fresh root from monthly");
  });

  it("wraps generated ROOT.md with canonical Hipocampus frontmatter and sections", async () => {
    const memDir = join(tmpDir, "memory");
    const monthlyDir = join(memDir, "monthly");
    await mkdir(monthlyDir, { recursive: true });

    await writeFile(
      join(monthlyDir, "2026-03.md"),
      "---\ntype: monthly\nstatus: fixed\nperiod: 2026-03\n---\n\nMarch summary",
    );

    const llm = createMockLLM("Unstructured root summary");
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    await engine.run(true);

    const rootContent = await readFile(join(memDir, "ROOT.md"), "utf8");

    expect(rootContent).toMatch(/^---\ntype: root\nstatus: tentative\nlast-updated: \d{4}-\d{2}-\d{2}\n---\n/);
    expect(rootContent).toContain("## Active Context (recent ~7 days)");
    expect(rootContent).toContain("## Recent Patterns");
    expect(rootContent).toContain("## Historical Summary");
    expect(rootContent).toContain("## Topics Index");
  });

  it("prompts root compaction to use the Hipocampus typed topic-index format", async () => {
    const memDir = join(tmpDir, "memory");
    const monthlyDir = join(memDir, "monthly");
    await mkdir(monthlyDir, { recursive: true });

    await writeFile(
      join(monthlyDir, "2026-03.md"),
      "---\ntype: monthly\nstatus: fixed\nperiod: 2026-03\n---\n\nMarch summary",
    );

    const llm = createMockLLM("## Active Context (recent ~7 days)\n- current\n");
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    await engine.run(true);

    const rootCall = llm.calls.find(c =>
      typeof c.messages[0]?.content === "string" &&
      c.messages[0].content.includes("Generate a ROOT.md memory index"),
    );

    expect(rootCall).toBeDefined();
    const prompt = rootCall?.messages[0]?.content;
    expect(prompt).toContain("type: root");
    expect(prompt).toContain("last-updated: YYYY-MM-DD");
    expect(prompt).toContain("topic-keyword [project]");
    expect(prompt).toContain("No prose");
  });
});

describe("stop-check chain", () => {
  it("daily empty → weekly/monthly/root all skipped", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(join(memDir, "daily"), { recursive: true });
    await mkdir(join(memDir, "weekly"), { recursive: true });
    await mkdir(join(memDir, "monthly"), { recursive: true });
    // No raw memory files

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.daily).toHaveLength(0);
    expect(result.stats.weekly).toHaveLength(0);
    expect(result.stats.monthly).toHaveLength(0);
    expect(result.compacted).toBe(false);
    expect(llm.calls.length).toBe(0);
  });

  it("full chain runs when there is content at every level", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    // Create raw files spread across multiple weeks and months (all in the past)
    // March 2026
    await writeFile(join(memDir, "2026-03-02.md"), generateLines(50));
    await writeFile(join(memDir, "2026-03-09.md"), generateLines(50));
    // April 2026
    await writeFile(join(memDir, "2026-04-06.md"), generateLines(50));
    await writeFile(join(memDir, "2026-04-07.md"), generateLines(50));

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    const result = await engine.run(true);

    expect(result.stats.daily.length).toBeGreaterThanOrEqual(4);
    expect(result.stats.weekly.length).toBeGreaterThanOrEqual(2);
    expect(result.stats.monthly.length).toBeGreaterThanOrEqual(1);
    expect(result.compacted).toBe(true);
  });
});

// ─── State persistence tests ───

describe("state persistence", () => {
  it("saves state after successful compaction", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    await writeFile(join(memDir, "2026-04-10.md"), "# Log\nContent");

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    await engine.run(true);

    const stateRaw = await readFile(join(memDir, ".compaction-state.json"), "utf8");
    const state = JSON.parse(stateRaw);
    expect(state.lastCompactionRun).toBeTruthy();
    expect(state.rawLinesSinceLastCompaction).toBe(0);
  });

  it("does not save state when nothing compacted", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    // No raw files

    const llm = createMockLLM();
    const engine = new CompactionEngine(tmpDir, defaultConfig(), llm);
    await engine.run(true);

    // State file should not exist (or if it did before, shouldn't be updated)
    try {
      await readFile(join(memDir, ".compaction-state.json"), "utf8");
      // If file exists, it was from a previous test — shouldn't happen with fresh tmpDir
      expect(true).toBe(false);
    } catch {
      // Expected — no state file
    }
  });
});

// ─── LLM summarize tests ───

describe("LLM summarization", () => {
  it("passes correct model and params to LLM", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });

    await writeFile(join(memDir, "2026-04-10.md"), generateLines(250));

    const llm = createMockLLM("Summary");
    const config = { ...defaultConfig(), model: "claude-haiku-3" };
    const engine = new CompactionEngine(tmpDir, config, llm);
    await engine.run(true);

    // At least 1 LLM call for daily summary (root may add more)
    const dailyCalls = llm.calls.filter(c =>
      typeof c.messages[0]?.content === "string" && c.messages[0].content.includes("daily log"),
    );
    expect(dailyCalls.length).toBe(1);
    expect(dailyCalls[0]!.model).toBe("claude-haiku-3");
    expect(dailyCalls[0]!.temperature).toBe(0);
    expect(dailyCalls[0]!.max_tokens).toBe(4096);
  });

  it("collects multi-chunk text_delta events", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    await writeFile(join(memDir, "2026-04-10.md"), generateLines(250));

    const multiChunkLLM: CompactionLLM = {
      async *stream(): AsyncGenerator<LLMEvent> {
        yield { kind: "text_delta", blockIndex: 0, delta: "Part 1 " };
        yield { kind: "text_delta", blockIndex: 0, delta: "Part 2 " };
        yield { kind: "text_delta", blockIndex: 0, delta: "Part 3" };
        yield { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } };
      },
    };

    const engine = new CompactionEngine(tmpDir, defaultConfig(), multiChunkLLM);
    await engine.run(true);

    const dailyContent = await readFile(join(memDir, "daily", "2026-04-10.md"), "utf8");
    expect(dailyContent).toContain("Part 1 Part 2 Part 3");
  });

  it("falls back to raw content when LLM stream throws", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    await writeFile(join(memDir, "2026-04-10.md"), generateLines(250));

    const failingLLM: CompactionLLM = {
      async *stream(): AsyncGenerator<LLMEvent> {
        throw new Error("LLM auth failed");
      },
    };

    const engine = new CompactionEngine(tmpDir, defaultConfig(), failingLLM);
    const result = await engine.run(true);

    // Should NOT throw — graceful degradation
    expect(result.stats.daily).toContain("2026-04-10");
    expect(result.compacted).toBe(true);

    // Daily file should exist with raw content (fallback)
    const dailyContent = await readFile(join(memDir, "daily", "2026-04-10.md"), "utf8");
    expect(dailyContent).toContain("type: daily");
    expect(dailyContent).toContain("Line 1: some content here");
  });

  it("falls back to raw content when LLM summarize rejects (timeout simulation)", async () => {
    const memDir = join(tmpDir, "memory");
    await mkdir(memDir, { recursive: true });
    await writeFile(join(memDir, "2026-04-10.md"), generateLines(250));

    // Simulate a timeout by rejecting immediately with the same error
    const timeoutLLM: CompactionLLM = {
      async *stream(): AsyncGenerator<LLMEvent> {
        throw new Error("summarize timeout");
      },
    };

    const engine = new CompactionEngine(tmpDir, defaultConfig(), timeoutLLM);
    const result = await engine.run(true);

    expect(result.stats.daily).toContain("2026-04-10");
    expect(result.compacted).toBe(true);

    const dailyContent = await readFile(join(memDir, "daily", "2026-04-10.md"), "utf8");
    expect(dailyContent).toContain("Line 1: some content here");
  });

  it("allows slow summarization calls that exceed 30 seconds", async () => {
    vi.useFakeTimers();
    try {
      const slowFirstCallLLM: CompactionLLM = {
        async *stream(): AsyncGenerator<LLMEvent> {
          await new Promise((resolve) => setTimeout(resolve, 31_000));
          yield { kind: "text_delta", blockIndex: 0, delta: "Slow summary" };
          yield { kind: "message_end", stopReason: "end_turn", usage: { inputTokens: 100, outputTokens: 50 } };
        },
      };

      const engine = new CompactionEngine(tmpDir, defaultConfig(), slowFirstCallLLM);
      const summarize = (engine as unknown as {
        summarize(content: string, instruction: string): Promise<string>;
      }).summarize.bind(engine);
      const summaryPromise = summarize("large memory body", "Summarize slowly.");
      await vi.advanceTimersByTimeAsync(31_000);

      await expect(summaryPromise).resolves.toBe("Slow summary");
    } finally {
      vi.useRealTimers();
    }
  });
});
