/**
 * Built-in benchmark-verifier hook — T3-15 (OMC Port B).
 *
 * Design reference:
 * - `docs/plans/2026-04-19-core-agent-phase-3-plan.md` §5 / T3-15.
 * - `docs/notes/2026-04-19-omc-self-improve-port-analysis.md` Port B.
 *
 * Empirical counterpart to §7.13 answer-verifier. Where answerVerifier
 * uses a Haiku judge to decide whether the drafted answer *semantically*
 * addresses the user's ask, benchmarkVerifier runs a user-configured
 * machine-verifiable check (tests, arithmetic, benchmark stats) and
 * compares the extracted metric against a stored baseline. On a
 * regression the commit is blocked with `[RETRY:BENCHMARK] …` so the
 * existing retry loop can give the model one more attempt.
 *
 * Opt-in: the hook is a no-op unless `workspace/agent.config.yaml`
 * contains a `benchmark:` key:
 *
 *   benchmark:
 *     command: "npm test --silent -- --reporter=json"
 *     metric: "stats.pass"          # dot-path into parsed JSON stdout
 *     direction: "max"               # "max" | "min"
 *     baseline_path: ".benchmark-baseline.json"
 *     timeout_ms: 60000
 *
 * Fail-open policy (benchmark infra must never trap a turn):
 * - Command times out      → warn, continue.
 * - Non-zero exit           → warn, continue.
 * - Stdout not parseable    → warn, continue.
 * - Metric path missing     → warn, continue.
 * - Baseline file unreadable→ treat as first run, continue.
 *
 * Retry semantics (shared with §7.13):
 * - On regression with `retryCount === 0` → block with `[RETRY:BENCHMARK] …`.
 * - On regression with `retryCount >= 1`  → continue (budget exhausted)
 *   and emit a warning audit event.
 *
 * Baseline update:
 * - First run (no baseline file) → write current metric as baseline,
 *   continue.
 * - `direction=max` and current > baseline → update baseline.
 * - `direction=min` and current < baseline → update baseline.
 *
 * Env toggle: `CORE_AGENT_BENCHMARK_VERIFY=off` disables the hook.
 */

import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type { RegisteredHook, HookContext } from "../types.js";
import { withClawyBinPath } from "../../util/shellPath.js";

const MAX_RETRIES = 1;
const DEFAULT_TIMEOUT_MS = 60_000;
const MAX_TIMEOUT_MS = 600_000;
const MAX_STDOUT_BYTES = 2 * 1024 * 1024;

export type Direction = "max" | "min";

export interface BenchmarkConfig {
  command: string;
  metric: string;
  direction: Direction;
  baseline_path: string;
  timeout_ms: number;
}

export interface RunResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  timedOut: boolean;
  durationMs: number;
}

interface BaselineFile {
  metric: number;
  direction: Direction;
  updatedAt: string;
}

function isEnabledByEnv(): boolean {
  const raw = process.env.CORE_AGENT_BENCHMARK_VERIFY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  if (v === "" || v === "on" || v === "true" || v === "1") return true;
  return false;
}

/**
 * Exported for tests — parses the `benchmark` block out of
 * `agent.config.yaml`. Returns null if absent or malformed (caller
 * treats that as a no-op).
 */
export async function readBenchmarkConfig(
  workspaceRoot: string,
): Promise<BenchmarkConfig | null> {
  const configPath = path.join(workspaceRoot, "agent.config.yaml");
  let raw: string;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const block = (parsed as Record<string, unknown>)["benchmark"];
  if (!block || typeof block !== "object") return null;
  const b = block as Record<string, unknown>;
  const command = typeof b.command === "string" ? b.command.trim() : "";
  const metric = typeof b.metric === "string" ? b.metric.trim() : "";
  const directionRaw = typeof b.direction === "string" ? b.direction.trim().toLowerCase() : "";
  const baselinePath = typeof b.baseline_path === "string" ? b.baseline_path.trim() : "";
  const timeoutRaw = b.timeout_ms;
  if (command.length === 0 || metric.length === 0 || baselinePath.length === 0) {
    return null;
  }
  if (directionRaw !== "max" && directionRaw !== "min") return null;
  let timeoutMs = DEFAULT_TIMEOUT_MS;
  if (typeof timeoutRaw === "number" && Number.isFinite(timeoutRaw) && timeoutRaw > 0) {
    timeoutMs = Math.min(MAX_TIMEOUT_MS, Math.floor(timeoutRaw));
  }
  return {
    command,
    metric,
    direction: directionRaw,
    baseline_path: baselinePath,
    timeout_ms: timeoutMs,
  };
}

/**
 * Resolve a dot-path like `stats.pass` or `results[0].score` into a
 * parsed-JSON object. Returns undefined if any segment is missing.
 * Supports simple `[<n>]` integer index segments on top of dotted keys.
 */
export function resolveDotPath(obj: unknown, pathStr: string): unknown {
  if (obj === undefined || obj === null) return undefined;
  if (!pathStr) return obj;
  const segments: Array<string | number> = [];
  const re = /[^.[\]]+|\[(\d+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(pathStr)) !== null) {
    if (m[1] !== undefined) {
      segments.push(Number.parseInt(m[1], 10));
    } else {
      segments.push(m[0]);
    }
  }
  let cur: unknown = obj;
  for (const seg of segments) {
    if (cur === null || cur === undefined) return undefined;
    if (typeof seg === "number") {
      if (!Array.isArray(cur)) return undefined;
      cur = cur[seg];
    } else {
      if (typeof cur !== "object") return undefined;
      cur = (cur as Record<string, unknown>)[seg];
    }
  }
  return cur;
}

/**
 * Execute the configured benchmark command. Kept exported so unit
 * tests can import and (optionally) drive it against a fake command.
 * Uses `/bin/sh -c` — same surface as the Bash tool; the command
 * string comes from the bot operator's own `agent.config.yaml`, never
 * from untrusted user input.
 */
export function runBenchmarkCommand(
  command: string,
  cwd: string,
  timeoutMs: number,
): Promise<RunResult> {
  const started = Date.now();
  return new Promise<RunResult>((resolve) => {
    let settled = false;
    let timedOut = false;
    const finish = (res: Omit<RunResult, "durationMs">): void => {
      if (settled) return;
      settled = true;
      resolve({ ...res, durationMs: Date.now() - started });
    };
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn("/bin/sh", ["-c", command], {
        cwd,
        env: { ...withClawyBinPath(process.env), PWD: cwd },
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (err) {
      finish({
        ok: false,
        stdout: "",
        stderr: `spawn failed: ${String(err)}`,
        exitCode: null,
        signal: null,
        timedOut: false,
      });
      return;
    }

    let stdout = "";
    let stderr = "";
    let stdoutTruncated = false;
    const capture = (chunk: Buffer, which: "stdout" | "stderr"): void => {
      const cur = which === "stdout" ? stdout : stderr;
      if (cur.length >= MAX_STDOUT_BYTES) {
        if (which === "stdout") stdoutTruncated = true;
        return;
      }
      const room = MAX_STDOUT_BYTES - cur.length;
      const piece = chunk.toString("utf8").slice(0, room);
      if (which === "stdout") stdout += piece;
      else stderr += piece;
    };
    child.stdout?.on("data", (c: Buffer) => capture(c, "stdout"));
    child.stderr?.on("data", (c: Buffer) => capture(c, "stderr"));

    const killTimer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGKILL");
      } catch {
        /* ignore */
      }
    }, timeoutMs);

    child.on("error", (err) => {
      clearTimeout(killTimer);
      finish({
        ok: false,
        stdout,
        stderr: stderr + `\nspawn error: ${String(err)}`,
        exitCode: null,
        signal: null,
        timedOut,
      });
    });
    child.on("close", (code, signal) => {
      clearTimeout(killTimer);
      if (stdoutTruncated) {
        stderr += "\n[stdout truncated]";
      }
      finish({
        ok: !timedOut && code === 0,
        stdout,
        stderr,
        exitCode: code,
        signal,
        timedOut,
      });
    });
  });
}

async function readBaseline(fullPath: string): Promise<BaselineFile | null> {
  let raw: string;
  try {
    raw = await fs.readFile(fullPath, "utf8");
  } catch {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const p = parsed as Record<string, unknown>;
    if (typeof p.metric !== "number" || !Number.isFinite(p.metric)) return null;
    const dir = p.direction === "min" ? "min" : "max";
    const updatedAt = typeof p.updatedAt === "string" ? p.updatedAt : "";
    return { metric: p.metric, direction: dir, updatedAt };
  } catch {
    return null;
  }
}

async function writeBaseline(
  fullPath: string,
  metric: number,
  direction: Direction,
): Promise<void> {
  const dir = path.dirname(fullPath);
  await fs.mkdir(dir, { recursive: true });
  const data: BaselineFile = {
    metric,
    direction,
    updatedAt: new Date().toISOString(),
  };
  await fs.writeFile(fullPath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

/**
 * A regression = the new metric is worse than the baseline per
 * direction. `max`: lower is worse. `min`: higher is worse.
 */
export function isRegression(
  current: number,
  baseline: number,
  direction: Direction,
): boolean {
  if (!Number.isFinite(current) || !Number.isFinite(baseline)) return false;
  if (direction === "max") return current < baseline;
  return current > baseline;
}

/** An improvement triggers a baseline update. */
export function isImprovement(
  current: number,
  baseline: number,
  direction: Direction,
): boolean {
  if (!Number.isFinite(current) || !Number.isFinite(baseline)) return false;
  if (direction === "max") return current > baseline;
  return current < baseline;
}

export interface BenchmarkVerifierOptions {
  workspaceRoot: string;
  /** Test hook — override the runner. */
  run?: (command: string, cwd: string, timeoutMs: number) => Promise<RunResult>;
}

export function makeBenchmarkVerifierHook(
  opts: BenchmarkVerifierOptions,
): RegisteredHook<"beforeCommit"> {
  const runner = opts.run ?? runBenchmarkCommand;
  return {
    name: "builtin:benchmark-verifier",
    point: "beforeCommit",
    priority: 85,
    blocking: true,
    timeoutMs: MAX_TIMEOUT_MS + 1_000,
    handler: async ({ retryCount }, ctx: HookContext) => {
      if (!isEnabledByEnv()) return { action: "continue" };

      const config = await readBenchmarkConfig(opts.workspaceRoot);
      if (!config) return { action: "continue" };

      const baselineFullPath = path.isAbsolute(config.baseline_path)
        ? config.baseline_path
        : path.join(opts.workspaceRoot, config.baseline_path);

      const runResult = await runner(
        config.command,
        opts.workspaceRoot,
        config.timeout_ms,
      );

      // Fail-open: benchmark infra failure must NEVER block a turn.
      if (runResult.timedOut) {
        ctx.log("warn", "[benchmarkVerifier] command timed out — failing open", {
          timeoutMs: config.timeout_ms,
          durationMs: runResult.durationMs,
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "benchmark-verifier",
          verdict: "ok",
          detail: `benchmark_timeout durationMs=${runResult.durationMs}`,
        });
        return { action: "continue" };
      }

      if (!runResult.ok) {
        ctx.log("warn", "[benchmarkVerifier] command failed — failing open", {
          exitCode: runResult.exitCode,
          signal: runResult.signal,
          durationMs: runResult.durationMs,
          stderr: runResult.stderr.slice(0, 512),
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "benchmark-verifier",
          verdict: "ok",
          detail: `benchmark_command_failed exitCode=${runResult.exitCode ?? "null"}`,
        });
        return { action: "continue" };
      }

      let parsedStdout: unknown;
      try {
        parsedStdout = JSON.parse(runResult.stdout);
      } catch {
        ctx.log("warn", "[benchmarkVerifier] stdout not JSON — failing open", {
          stdoutPreview: runResult.stdout.slice(0, 256),
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "benchmark-verifier",
          verdict: "ok",
          detail: "benchmark_stdout_not_json",
        });
        return { action: "continue" };
      }

      const metricValue = resolveDotPath(parsedStdout, config.metric);
      if (typeof metricValue !== "number" || !Number.isFinite(metricValue)) {
        ctx.log("warn", "[benchmarkVerifier] metric not found/not a number — failing open", {
          metricPath: config.metric,
          metricType: typeof metricValue,
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "benchmark-verifier",
          verdict: "ok",
          detail: `benchmark_metric_missing path=${config.metric}`,
        });
        return { action: "continue" };
      }

      const current = metricValue;
      const baseline = await readBaseline(baselineFullPath);

      // First run — establish baseline and let the commit proceed.
      if (!baseline) {
        try {
          await writeBaseline(baselineFullPath, current, config.direction);
        } catch (err) {
          ctx.log("warn", "[benchmarkVerifier] baseline write failed — failing open", {
            error: String(err),
            baselinePath: baselineFullPath,
          });
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "benchmark-verifier",
          verdict: "ok",
          detail: `benchmark_baseline_initialized current=${current} direction=${config.direction}`,
        });
        ctx.log("info", "[benchmarkVerifier] baseline_initialized", {
          current,
          direction: config.direction,
          baselinePath: baselineFullPath,
        });
        return { action: "continue" };
      }

      if (isRegression(current, baseline.metric, config.direction)) {
        const delta = current - baseline.metric;
        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[benchmarkVerifier] regression after retry — failing open", {
            current,
            baseline: baseline.metric,
            direction: config.direction,
            delta,
            retryCount,
          });
          ctx.emit({
            type: "rule_check",
            ruleId: "benchmark-verifier",
            verdict: "violation",
            detail: `benchmark_regression_retry_exhausted current=${current} baseline=${baseline.metric} direction=${config.direction} delta=${delta}`,
          });
          return { action: "continue" };
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "benchmark-verifier",
          verdict: "violation",
          detail: `benchmark_regression current=${current} baseline=${baseline.metric} direction=${config.direction} delta=${delta}`,
        });
        ctx.log("warn", "[benchmarkVerifier] blocking commit for retry", {
          current,
          baseline: baseline.metric,
          direction: config.direction,
          delta,
          retryCount,
        });
        return {
          action: "block",
          reason: `[RETRY:BENCHMARK] delta=${delta} direction=${config.direction} current=${current} baseline=${baseline.metric}. Benchmark metric "${config.metric}" regressed. Investigate the change and retry with a fix that restores or improves the metric.`,
        };
      }

      // Improvement → update baseline.
      if (isImprovement(current, baseline.metric, config.direction)) {
        try {
          await writeBaseline(baselineFullPath, current, config.direction);
        } catch (err) {
          ctx.log("warn", "[benchmarkVerifier] baseline update failed — continuing", {
            error: String(err),
            baselinePath: baselineFullPath,
          });
        }
      }

      ctx.emit({
        type: "rule_check",
        ruleId: "benchmark-verifier",
        verdict: "ok",
        detail: `benchmark_verified current=${current} baseline=${baseline.metric} direction=${config.direction}`,
      });
      ctx.log("info", "[benchmarkVerifier] benchmark_verified", {
        current,
        baseline: baseline.metric,
        direction: config.direction,
      });

      return { action: "continue" };
    },
  };
}
