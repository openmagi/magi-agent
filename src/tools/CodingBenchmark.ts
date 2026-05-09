import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";

export type CodingBenchmarkOutcome = "passed" | "failed" | "blocked";

export interface CodingBenchmarkInput {
  action: "record" | "summary";
  taskId?: string;
  category?: string;
  outcome?: CodingBenchmarkOutcome;
  testsPassed?: boolean;
  retryCount?: number;
  wrongCompletionClaims?: number;
  filesChanged?: string[];
  notes?: string;
}

export interface CodingBenchmarkRecord {
  ts: number;
  turnId: string;
  taskId: string;
  category: string;
  outcome: CodingBenchmarkOutcome;
  testsPassed: boolean;
  retryCount: number;
  wrongCompletionClaims: number;
  filesChanged: string[];
  notes?: string;
}

export interface CodingBenchmarkSummary {
  totalRuns: number;
  passedRuns: number;
  failedRuns: number;
  blockedRuns: number;
  successRate: number;
  averageRetryCount: number;
  wrongCompletionClaimRate: number;
}

export interface CodingBenchmarkOutput {
  path: string;
  records: CodingBenchmarkRecord[];
  summary: CodingBenchmarkSummary;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["record", "summary"] },
    taskId: { type: "string" },
    category: { type: "string" },
    outcome: { type: "string", enum: ["passed", "failed", "blocked"] },
    testsPassed: { type: "boolean" },
    retryCount: { type: "integer", minimum: 0 },
    wrongCompletionClaims: { type: "integer", minimum: 0 },
    filesChanged: { type: "array", items: { type: "string" } },
    notes: { type: "string" },
  },
  required: ["action"],
} as const;

export function makeCodingBenchmarkTool(
  workspaceRoot: string,
): Tool<CodingBenchmarkInput, CodingBenchmarkOutput> {
  return {
    name: "CodingBenchmark",
    description:
      "Record and summarize coding-agent benchmark outcomes as structured metrics: success rate, retry count, wrong completion claim rate, files changed, and task category.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    validate(input) {
      if (!input || (input.action !== "record" && input.action !== "summary")) {
        return "`action` must be 'record' or 'summary'";
      }
      if (input.action === "record") {
        if (!input.taskId) return "`taskId` is required when action='record'";
        if (!input.category) return "`category` is required when action='record'";
        if (
          input.outcome !== "passed" &&
          input.outcome !== "failed" &&
          input.outcome !== "blocked"
        ) {
          return "`outcome` must be 'passed', 'failed', or 'blocked' when action='record'";
        }
        if (typeof input.testsPassed !== "boolean") {
          return "`testsPassed` is required when action='record'";
        }
      }
      return null;
    },
    async execute(
      input: CodingBenchmarkInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CodingBenchmarkOutput>> {
      const start = Date.now();
      try {
        const storePath = benchmarkStorePath(workspaceRoot);
        await fs.mkdir(path.dirname(storePath), { recursive: true });
        if (input.action === "record") {
          const record: CodingBenchmarkRecord = {
            ts: Date.now(),
            turnId: ctx.turnId,
            taskId: input.taskId!,
            category: input.category!,
            outcome: input.outcome!,
            testsPassed: input.testsPassed!,
            retryCount: Math.max(0, input.retryCount ?? 0),
            wrongCompletionClaims: Math.max(0, input.wrongCompletionClaims ?? 0),
            filesChanged: input.filesChanged ?? [],
            ...(input.notes ? { notes: input.notes } : {}),
          };
          await fs.appendFile(storePath, `${JSON.stringify(record)}\n`, "utf8");
        }
        const records = await readRecords(storePath);
        const summary = summarize(records);
        const output: CodingBenchmarkOutput = {
          path: relativeStorePath(workspaceRoot, storePath),
          records,
          summary,
        };
        return {
          status: "ok",
          output,
          metadata: {
            evidenceKind: "benchmark",
            totalRuns: summary.totalRuns,
            successRate: summary.successRate,
            wrongCompletionClaimRate: summary.wrongCompletionClaimRate,
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return {
          status: "error",
          errorCode: "benchmark_failed",
          errorMessage: err instanceof Error ? err.message : String(err),
          durationMs: Date.now() - start,
        };
      }
    },
  };
}

function benchmarkStorePath(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".magi", "coding-benchmark-runs.jsonl");
}

function relativeStorePath(workspaceRoot: string, storePath: string): string {
  return path.relative(workspaceRoot, storePath).split(path.sep).join("/");
}

async function readRecords(storePath: string): Promise<CodingBenchmarkRecord[]> {
  let raw = "";
  try {
    raw = await fs.readFile(storePath, "utf8");
  } catch {
    return [];
  }
  const records: CodingBenchmarkRecord[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const parsed = JSON.parse(line) as CodingBenchmarkRecord;
      if (parsed && typeof parsed.taskId === "string") records.push(parsed);
    } catch {
      /* ignore malformed historical rows */
    }
  }
  return records;
}

function summarize(records: readonly CodingBenchmarkRecord[]): CodingBenchmarkSummary {
  const totalRuns = records.length;
  const passedRuns = records.filter((record) => record.outcome === "passed").length;
  const failedRuns = records.filter((record) => record.outcome === "failed").length;
  const blockedRuns = records.filter((record) => record.outcome === "blocked").length;
  const retryTotal = records.reduce((sum, record) => sum + record.retryCount, 0);
  const wrongClaimTotal = records.reduce(
    (sum, record) => sum + record.wrongCompletionClaims,
    0,
  );
  return {
    totalRuns,
    passedRuns,
    failedRuns,
    blockedRuns,
    successRate: totalRuns === 0 ? 0 : passedRuns / totalRuns,
    averageRetryCount: totalRuns === 0 ? 0 : retryTotal / totalRuns,
    wrongCompletionClaimRate: totalRuns === 0 ? 0 : wrongClaimTotal / totalRuns,
  };
}
