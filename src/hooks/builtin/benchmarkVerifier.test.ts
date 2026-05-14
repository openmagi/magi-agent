/**
 * benchmarkVerifier unit tests — T3-15 (OMC Port B).
 *
 * Coverage:
 *   1. No benchmark config → noop.
 *   2. First run (no baseline file) → creates baseline, continues.
 *   3. Current beats baseline (direction=max) → continues + baseline updated.
 *   4. Current worse than baseline + retryCount=0 → blocks.
 *   5. Current worse than baseline + retryCount=1 → warns + continues.
 *   6. Command times out → fail-open (warn, continue).
 *   7. Command fails (non-zero exit, stdout not JSON) → fail-open.
 *
 * The runner is injected (opts.run) so we don't depend on spawning real
 * shells — identical test-infra pattern to SpawnAgent / Bash tests.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  makeBenchmarkVerifierHook,
  resolveDotPath,
  isRegression,
  isImprovement,
  type RunResult,
} from "./benchmarkVerifier.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";

async function tmpDir(prefix: string): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), prefix));
}

async function writeConfig(workspaceRoot: string, yaml: string): Promise<void> {
  await fs.writeFile(path.join(workspaceRoot, "agent.config.yaml"), yaml, "utf8");
}

function makeCtx(): {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string; data?: object }>;
} {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string; data?: object }> = [];
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "sess-test",
    turnId: "turn-test",
    llm: {} as unknown as LLMClient,
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg, data) => logs.push({ level, msg, data }),
    abortSignal: new AbortController().signal,
    deadlineMs: 60_000,
  };
  return { ctx, emitted, logs };
}

function fakeRunner(result: Partial<RunResult>): RunResult {
  return {
    ok: result.ok ?? true,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.exitCode ?? (result.ok === false ? 1 : 0),
    signal: result.signal ?? null,
    timedOut: result.timedOut ?? false,
    durationMs: result.durationMs ?? 1,
  };
}

describe("benchmarkVerifier hook", () => {
  const originalEnv = process.env.MAGI_BENCHMARK_VERIFY;

  beforeEach(() => {
    delete process.env.MAGI_BENCHMARK_VERIFY;
  });

  afterEach(() => {
    if (originalEnv === undefined) delete process.env.MAGI_BENCHMARK_VERIFY;
    else process.env.MAGI_BENCHMARK_VERIFY = originalEnv;
  });

  it("no benchmark config → noop", async () => {
    const workspaceRoot = await tmpDir("bench-nocfg-");
    // no agent.config.yaml at all
    let ran = false;
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () => {
        ran = true;
        return fakeRunner({ ok: true, stdout: "{}" });
      },
    });
    const { ctx, emitted, logs } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "hi",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hello",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    expect(ran).toBe(false);
    expect(emitted).toEqual([]);
    expect(logs).toEqual([]);
  });

  it("first run (no baseline file) → creates baseline, continues", async () => {
    const workspaceRoot = await tmpDir("bench-first-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "echo skipped"',
        '  metric: "stats.pass"',
        '  direction: "max"',
        '  baseline_path: ".benchmark-baseline.json"',
        "  timeout_ms: 60000",
      ].join("\n"),
    );
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () => fakeRunner({ ok: true, stdout: JSON.stringify({ stats: { pass: 42 } }) }),
    });
    const { ctx, emitted } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "run",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    const baselinePath = path.join(workspaceRoot, ".benchmark-baseline.json");
    const raw = await fs.readFile(baselinePath, "utf8");
    const parsed = JSON.parse(raw);
    expect(parsed.metric).toBe(42);
    expect(parsed.direction).toBe("max");
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.ruleId === "benchmark-verifier" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("benchmark_baseline_initialized"),
      ),
    ).toBe(true);
  });

  it("current beats baseline (direction=max) → continues + baseline updated", async () => {
    const workspaceRoot = await tmpDir("bench-beat-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "noop"',
        '  metric: "score"',
        '  direction: "max"',
        '  baseline_path: ".bench.json"',
      ].join("\n"),
    );
    const baselinePath = path.join(workspaceRoot, ".bench.json");
    await fs.writeFile(
      baselinePath,
      JSON.stringify({ metric: 50, direction: "max", updatedAt: "t0" }),
      "utf8",
    );
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () => fakeRunner({ ok: true, stdout: JSON.stringify({ score: 75 }) }),
    });
    const { ctx, emitted } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "u",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    const parsed = JSON.parse(await fs.readFile(baselinePath, "utf8"));
    expect(parsed.metric).toBe(75);
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("benchmark_verified"),
      ),
    ).toBe(true);
  });

  it("current worse than baseline + retryCount=0 → blocks with [RETRY:BENCHMARK]", async () => {
    const workspaceRoot = await tmpDir("bench-regress-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "noop"',
        '  metric: "score"',
        '  direction: "max"',
        '  baseline_path: ".bench.json"',
      ].join("\n"),
    );
    const baselinePath = path.join(workspaceRoot, ".bench.json");
    await fs.writeFile(
      baselinePath,
      JSON.stringify({ metric: 80, direction: "max", updatedAt: "t0" }),
      "utf8",
    );
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () => fakeRunner({ ok: true, stdout: JSON.stringify({ score: 60 }) }),
    });
    const { ctx, emitted } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "u",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toBeDefined();
    expect(res!.action).toBe("block");
    if (res && res.action === "block") {
      expect(res.reason).toContain("[RETRY:BENCHMARK]");
      expect(res.reason).toContain("current=60");
      expect(res.reason).toContain("baseline=80");
      expect(res.reason).toContain("direction=max");
      expect(res.reason).toContain("delta=-20");
    }
    // Baseline must NOT be overwritten on regression.
    const parsed = JSON.parse(await fs.readFile(baselinePath, "utf8"));
    expect(parsed.metric).toBe(80);
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.verdict === "violation" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("benchmark_regression "),
      ),
    ).toBe(true);
  });

  it("current worse than baseline + retryCount=1 → warns + continues (fail open)", async () => {
    const workspaceRoot = await tmpDir("bench-regress-retry-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "noop"',
        '  metric: "score"',
        '  direction: "max"',
        '  baseline_path: ".bench.json"',
      ].join("\n"),
    );
    const baselinePath = path.join(workspaceRoot, ".bench.json");
    await fs.writeFile(
      baselinePath,
      JSON.stringify({ metric: 80, direction: "max", updatedAt: "t0" }),
      "utf8",
    );
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () => fakeRunner({ ok: true, stdout: JSON.stringify({ score: 60 }) }),
    });
    const { ctx, emitted, logs } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "u",
        retryCount: 1,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.verdict === "violation" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("benchmark_regression_retry_exhausted"),
      ),
    ).toBe(true);
    expect(logs.some((l) => l.level === "warn")).toBe(true);
  });

  it("command times out → fail-open (warn, continue)", async () => {
    const workspaceRoot = await tmpDir("bench-timeout-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "noop"',
        '  metric: "score"',
        '  direction: "max"',
        '  baseline_path: ".bench.json"',
        "  timeout_ms: 100",
      ].join("\n"),
    );
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () =>
        fakeRunner({ ok: false, timedOut: true, stdout: "", stderr: "", exitCode: null }),
    });
    const { ctx, emitted, logs } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "u",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    expect(logs.some((l) => l.level === "warn" && l.msg.includes("timed out"))).toBe(true);
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("benchmark_timeout"),
      ),
    ).toBe(true);
  });

  it("command fails (non-zero exit, stdout not JSON) → fail-open", async () => {
    const workspaceRoot = await tmpDir("bench-fail-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "noop"',
        '  metric: "score"',
        '  direction: "max"',
        '  baseline_path: ".bench.json"',
      ].join("\n"),
    );
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () =>
        fakeRunner({
          ok: false,
          exitCode: 2,
          stdout: "this is not json",
          stderr: "oops",
        }),
    });
    const { ctx, emitted } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "u",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("benchmark_command_failed"),
      ),
    ).toBe(true);
  });

  it("env=off → noop regardless of config", async () => {
    process.env.MAGI_BENCHMARK_VERIFY = "off";
    const workspaceRoot = await tmpDir("bench-envoff-");
    await writeConfig(
      workspaceRoot,
      [
        "benchmark:",
        '  command: "noop"',
        '  metric: "score"',
        '  direction: "max"',
        '  baseline_path: ".bench.json"',
      ].join("\n"),
    );
    let ran = false;
    const hook = makeBenchmarkVerifierHook({
      workspaceRoot,
      run: async () => {
        ran = true;
        return fakeRunner({ ok: true, stdout: "{}" });
      },
    });
    const { ctx } = makeCtx();
    const res = await hook.handler(
      {
        assistantText: "ok",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "u",
        retryCount: 0,
      },
      ctx,
    );
    expect(res).toEqual({ action: "continue" });
    expect(ran).toBe(false);
  });

  it("hook contract: priority 85, beforeCommit, blocking", () => {
    const hook = makeBenchmarkVerifierHook({ workspaceRoot: "/tmp/unused" });
    expect(hook.name).toBe("builtin:benchmark-verifier");
    expect(hook.point).toBe("beforeCommit");
    expect(hook.priority).toBe(85);
    expect(hook.blocking).toBe(true);
  });
});

describe("resolveDotPath", () => {
  it("resolves nested keys", () => {
    expect(resolveDotPath({ a: { b: { c: 7 } } }, "a.b.c")).toBe(7);
  });
  it("resolves array indices", () => {
    expect(resolveDotPath({ xs: [10, 20, 30] }, "xs[1]")).toBe(20);
  });
  it("returns undefined for missing key", () => {
    expect(resolveDotPath({ a: 1 }, "a.b.c")).toBeUndefined();
  });
  it("returns root on empty path", () => {
    expect(resolveDotPath({ a: 1 }, "")).toEqual({ a: 1 });
  });
});

describe("isRegression / isImprovement", () => {
  it("direction=max", () => {
    expect(isRegression(50, 80, "max")).toBe(true);
    expect(isRegression(90, 80, "max")).toBe(false);
    expect(isRegression(80, 80, "max")).toBe(false);
    expect(isImprovement(90, 80, "max")).toBe(true);
    expect(isImprovement(80, 80, "max")).toBe(false);
  });
  it("direction=min", () => {
    expect(isRegression(90, 80, "min")).toBe(true);
    expect(isRegression(50, 80, "min")).toBe(false);
    expect(isImprovement(50, 80, "min")).toBe(true);
  });
});
