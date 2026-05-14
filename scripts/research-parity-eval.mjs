#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_BENCHMARK = "docs/notes/research-parity/benchmark-v1.json";
const DEFAULT_RUN = "docs/notes/research-parity/sample-run.json";

export async function loadJsonFile(filePath) {
  const text = await fs.readFile(filePath, "utf8");
  return JSON.parse(text);
}

export function normalizeScore(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(5, n));
}

export function evaluateResearchRun(benchmark, run) {
  validateBenchmark(benchmark);
  validateRun(run);
  if (run.benchmarkVersion !== benchmark.version) {
    throw new Error(
      `benchmark version mismatch: ${run.benchmarkVersion} != ${benchmark.version}`,
    );
  }

  const taskById = new Map(benchmark.tasks.map((task) => [task.id, task]));
  const seen = new Set();
  const failureSet = new Set();
  const taskReports = [];
  let weightedScoreSum = 0;
  let weightSum = 0;

  for (const result of run.results) {
    const task = taskById.get(result.taskId);
    if (!task) throw new Error(`unknown task id: ${result.taskId}`);
    if (seen.has(result.taskId)) throw new Error(`duplicate task id: ${result.taskId}`);
    seen.add(result.taskId);

    for (const category of result.failureCategories ?? []) {
      failureSet.add(category);
    }

    const score = averageRubricScore(benchmark.rubric, result.scores ?? {});
    const weight = normalizeWeight(task.weight);
    weightedScoreSum += score * weight;
    weightSum += weight;

    taskReports.push({
      taskId: task.id,
      category: task.category,
      score: round(score),
      weight,
      failureCategories: [...(result.failureCategories ?? [])].sort(),
      inspectedSourceCount: Array.isArray(result.inspectedSources)
        ? result.inspectedSources.length
        : 0,
      toolCallCount: Array.isArray(result.toolCalls)
        ? result.toolCalls.reduce((sum, call) => sum + normalizeCount(call.count), 0)
        : 0,
    });
  }

  const aggregateScore = round(weightSum === 0 ? 0 : weightedScoreSum / weightSum);
  const missingTaskIds = benchmark.tasks
    .map((task) => task.id)
    .filter((id) => !seen.has(id));

  return {
    ok: true,
    benchmark: benchmark.name,
    benchmarkVersion: benchmark.version,
    agent: run.agent,
    runId: run.runId,
    createdAt: run.createdAt,
    taskCount: benchmark.tasks.length,
    evaluatedCount: run.results.length,
    missingTaskIds,
    aggregateScore,
    threshold: thresholdForScore(aggregateScore),
    failureCategories: [...failureSet].sort(),
    tasks: taskReports,
  };
}

function validateBenchmark(benchmark) {
  if (!benchmark || typeof benchmark !== "object") {
    throw new Error("benchmark must be an object");
  }
  if (!Number.isInteger(benchmark.version)) {
    throw new Error("benchmark.version must be an integer");
  }
  if (typeof benchmark.name !== "string" || benchmark.name.length === 0) {
    throw new Error("benchmark.name is required");
  }
  if (!Array.isArray(benchmark.rubric) || benchmark.rubric.length === 0) {
    throw new Error("benchmark.rubric must be a non-empty array");
  }
  if (!Array.isArray(benchmark.tasks) || benchmark.tasks.length === 0) {
    throw new Error("benchmark.tasks must be a non-empty array");
  }

  const ids = new Set();
  for (const task of benchmark.tasks) {
    if (!task || typeof task !== "object") throw new Error("benchmark task must be an object");
    if (typeof task.id !== "string" || task.id.length === 0) {
      throw new Error("task.id is required");
    }
    if (ids.has(task.id)) throw new Error(`duplicate benchmark task id: ${task.id}`);
    ids.add(task.id);
    if (typeof task.category !== "string" || task.category.length === 0) {
      throw new Error(`task.category is required for ${task.id}`);
    }
    if (typeof task.prompt !== "string" || task.prompt.length === 0) {
      throw new Error(`task.prompt is required for ${task.id}`);
    }
  }
}

function validateRun(run) {
  if (!run || typeof run !== "object") throw new Error("run must be an object");
  if (!Number.isInteger(run.benchmarkVersion)) {
    throw new Error("run.benchmarkVersion must be an integer");
  }
  if (typeof run.agent !== "string" || run.agent.length === 0) {
    throw new Error("run.agent is required");
  }
  if (typeof run.runId !== "string" || run.runId.length === 0) {
    throw new Error("run.runId is required");
  }
  if (!Array.isArray(run.results)) throw new Error("run.results must be an array");
}

function averageRubricScore(rubric, scores) {
  const total = rubric.reduce((sum, key) => sum + normalizeScore(scores[key]), 0);
  return rubric.length === 0 ? 0 : total / rubric.length;
}

function normalizeWeight(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

function normalizeCount(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.trunc(n) : 0;
}

function round(value) {
  return Math.round(value * 1000) / 1000;
}

function thresholdForScore(score) {
  if (score >= 4.25) return "parity_candidate";
  if (score >= 3.5) return "near_parity";
  if (score >= 2.5) return "partial";
  return "needs_work";
}

function parseArgs(argv) {
  const parsed = {
    benchmark: DEFAULT_BENCHMARK,
    run: DEFAULT_RUN,
    out: "",
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--benchmark") parsed.benchmark = requireValue(argv, ++i, arg);
    else if (arg === "--run") parsed.run = requireValue(argv, ++i, arg);
    else if (arg === "--out") parsed.out = requireValue(argv, ++i, arg);
    else if (arg === "--help" || arg === "-h") parsed.help = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  return parsed;
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (!value) throw new Error(`${flag} requires a value`);
  return value;
}

function usage() {
  return [
    "Usage: node scripts/research-parity-eval.mjs [--benchmark path] [--run path] [--out path]",
    "",
    `Default benchmark: ${DEFAULT_BENCHMARK}`,
    `Default run: ${DEFAULT_RUN}`,
  ].join("\n");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  const benchmark = await loadJsonFile(args.benchmark);
  const run = await loadJsonFile(args.run);
  const report = evaluateResearchRun(benchmark, run);
  const text = `${JSON.stringify(report, null, 2)}\n`;
  process.stdout.write(text);
  if (args.out) {
    await fs.mkdir(path.dirname(args.out), { recursive: true });
    await fs.writeFile(args.out, text, "utf8");
  }
}

const thisFile = fileURLToPath(import.meta.url);
if (process.argv[1] && path.resolve(process.argv[1]) === thisFile) {
  main().catch((err) => {
    console.error(
      `research-parity-eval: ERROR: ${err instanceof Error ? err.message : String(err)}`,
    );
    process.exit(1);
  });
}
