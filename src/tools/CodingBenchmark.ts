import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";

export type CodingBenchmarkOutcome = "passed" | "failed" | "blocked";
export type CodingBenchmarkSuite = "coding-golden-v1";

export interface CodingBenchmarkInput {
  action: "record" | "summary" | "list_tasks" | "start_run" | "report";
  suite?: CodingBenchmarkSuite;
  runId?: string;
  taskIds?: string[];
  taskId?: string;
  category?: string;
  outcome?: CodingBenchmarkOutcome;
  testsPassed?: boolean;
  retryCount?: number;
  wrongCompletionClaims?: number;
  filesChanged?: string[];
  notes?: string;
}

export interface CodingGoldenTask {
  id: string;
  title: string;
  category: string;
  prompt: string;
  verificationCommands: string[];
  successCriteria: string[];
  files: Record<string, string>;
}

export interface CodingGoldenTaskInfo {
  id: string;
  title: string;
  category: string;
  prompt: string;
  verificationCommands: string[];
  successCriteria: string[];
}

export interface CodingGoldenRunTask {
  id: string;
  title: string;
  category: string;
  workspacePath: string;
  prompt: string;
  verificationCommands: string[];
  successCriteria: string[];
}

export interface CodingGoldenRun {
  runId: string;
  suite: CodingBenchmarkSuite;
  path: string;
  taskCount: number;
  tasks: CodingGoldenRunTask[];
}

export interface CodingBenchmarkRecord {
  ts: number;
  turnId: string;
  runId?: string;
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

export interface CodingBenchmarkReportGroup extends CodingBenchmarkSummary {
  testsPassRate: number;
}

export interface CodingBenchmarkCategoryReport extends CodingBenchmarkReportGroup {
  category: string;
}

export interface CodingBenchmarkTaskReport extends CodingBenchmarkReportGroup {
  taskId: string;
  category: string;
  runId?: string;
}

export interface CodingBenchmarkGoldenRunReport {
  runId: string;
  taskCount: number;
  recordedRuns: number;
  passedRuns: number;
  failedRuns: number;
  blockedRuns: number;
  successRate: number;
}

export interface CodingBenchmarkReport {
  generatedAt: string;
  jsonPath: string;
  markdownPath: string;
  summary: CodingBenchmarkSummary;
  byCategory: CodingBenchmarkCategoryReport[];
  byTask: CodingBenchmarkTaskReport[];
  goldenRuns: CodingBenchmarkGoldenRunReport[];
}

export interface CodingBenchmarkOutput {
  path: string;
  records: CodingBenchmarkRecord[];
  summary: CodingBenchmarkSummary;
  goldenTasks?: CodingGoldenTaskInfo[];
  goldenRun?: CodingGoldenRun;
  report?: CodingBenchmarkReport;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["record", "summary", "list_tasks", "start_run", "report"],
    },
    suite: { type: "string", enum: ["coding-golden-v1"] },
    runId: { type: "string" },
    taskIds: { type: "array", items: { type: "string" } },
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
      "Record and summarize coding-agent benchmark outcomes, list deterministic golden coding tasks, and materialize golden benchmark run workspaces with verification commands.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    mutatesWorkspace: true,
    isConcurrencySafe: false,
    validate(input) {
      if (
        !input ||
        (input.action !== "record" &&
          input.action !== "summary" &&
          input.action !== "list_tasks" &&
          input.action !== "start_run" &&
          input.action !== "report")
      ) {
        return "`action` must be 'record', 'summary', 'list_tasks', 'start_run', or 'report'";
      }
      if (input.runId !== undefined && normalizeRunId(input.runId) === null) {
        return "`runId` may only contain letters, numbers, dots, underscores, and dashes";
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
      if (input.action === "start_run") {
        if (input.suite !== undefined && input.suite !== "coding-golden-v1") {
          return "`suite` must be 'coding-golden-v1'";
        }
        if (input.taskIds !== undefined) {
          if (!Array.isArray(input.taskIds) || input.taskIds.length === 0) {
            return "`taskIds` must be a non-empty string array when provided";
          }
          const unknown = input.taskIds.find((taskId) => !GOLDEN_TASKS_BY_ID.has(taskId));
          if (unknown) return `unknown golden benchmark task: ${unknown}`;
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
        const normalizedRunId =
          input.runId === undefined ? undefined : normalizeRunId(input.runId);
        if (input.runId !== undefined && normalizedRunId === null) {
          throw new Error("runId may only contain letters, numbers, dots, underscores, and dashes");
        }
        let goldenTasks: CodingGoldenTaskInfo[] | undefined;
        let goldenRun: CodingGoldenRun | undefined;
        let report: CodingBenchmarkReport | undefined;
        if (input.action === "record") {
          const record: CodingBenchmarkRecord = {
            ts: Date.now(),
            turnId: ctx.turnId,
            ...(normalizedRunId ? { runId: normalizedRunId } : {}),
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
        if (input.action === "list_tasks") {
          goldenTasks = GOLDEN_TASKS.map(goldenTaskInfo);
        }
        if (input.action === "start_run") {
          goldenRun = await startGoldenRun(workspaceRoot, input);
          goldenTasks = goldenRun.tasks.map((task) => ({
            id: task.id,
            title: task.title,
            category: task.category,
            prompt: task.prompt,
            verificationCommands: task.verificationCommands,
            successCriteria: task.successCriteria,
          }));
        }
        const records = await readRecords(storePath);
        const summary = summarize(records);
        if (input.action === "report") {
          report = await writeBenchmarkReport(workspaceRoot, records, summary);
        }
        const output: CodingBenchmarkOutput = {
          path: relativeStorePath(workspaceRoot, storePath),
          records,
          summary,
          ...(goldenTasks ? { goldenTasks } : {}),
          ...(goldenRun ? { goldenRun } : {}),
          ...(report ? { report } : {}),
        };
        return {
          status: "ok",
          output,
          metadata: {
            evidenceKind: "benchmark",
            totalRuns: summary.totalRuns,
            successRate: summary.successRate,
            wrongCompletionClaimRate: summary.wrongCompletionClaimRate,
            ...(goldenRun ? { goldenRunId: goldenRun.runId, goldenTaskCount: goldenRun.taskCount } : {}),
            ...(report
              ? {
                  evidenceKind: "benchmark_report",
                  reportPath: report.jsonPath,
                  markdownPath: report.markdownPath,
                  goldenRunCount: report.goldenRuns.length,
                }
              : {}),
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

const GOLDEN_SUITE: CodingBenchmarkSuite = "coding-golden-v1";

const GOLDEN_TASKS: CodingGoldenTask[] = [
  {
    id: "js-bugfix-arithmetic",
    title: "Fix a small arithmetic regression",
    category: "bugfix",
    prompt:
      "Fix the failing arithmetic test without weakening tests or changing the public function names.",
    verificationCommands: ["npm test"],
    successCriteria: [
      "`npm test` passes in the task workspace",
      "The exported add and multiply functions keep their public names",
    ],
    files: {
      "package.json": `${JSON.stringify(
        {
          type: "module",
          scripts: { test: "node --test test/math.test.js" },
        },
        null,
        2,
      )}\n`,
      "src/math.js": [
        "export function add(a, b) {",
        "  return a - b;",
        "}",
        "",
        "export function multiply(a, b) {",
        "  return a * b;",
        "}",
        "",
      ].join("\n"),
      "test/math.test.js": [
        "import assert from \"node:assert/strict\";",
        "import test from \"node:test\";",
        "import { add, multiply } from \"../src/math.js\";",
        "",
        "test(\"add returns the sum of two numbers\", () => {",
        "  assert.equal(add(2, 3), 5);",
        "  assert.equal(add(-2, 3), 1);",
        "});",
        "",
        "test(\"multiply returns the product of two numbers\", () => {",
        "  assert.equal(multiply(4, 5), 20);",
        "});",
        "",
      ].join("\n"),
    },
  },
  {
    id: "js-feature-clamp",
    title: "Add a bounded clamp helper",
    category: "feature",
    prompt:
      "Implement the missing clamp(value, min, max) helper. Preserve the existing module API and make the supplied tests pass.",
    verificationCommands: ["npm test"],
    successCriteria: [
      "`npm test` passes in the task workspace",
      "clamp returns min for values below range and max for values above range",
      "clamp throws when min is greater than max",
    ],
    files: {
      "package.json": `${JSON.stringify(
        {
          type: "module",
          scripts: { test: "node --test test/range.test.js" },
        },
        null,
        2,
      )}\n`,
      "src/range.js": [
        "export function clamp(_value, _min, _max) {",
        "  throw new Error(\"not implemented\");",
        "}",
        "",
      ].join("\n"),
      "test/range.test.js": [
        "import assert from \"node:assert/strict\";",
        "import test from \"node:test\";",
        "import { clamp } from \"../src/range.js\";",
        "",
        "test(\"clamp keeps values inside bounds\", () => {",
        "  assert.equal(clamp(5, 0, 10), 5);",
        "  assert.equal(clamp(-1, 0, 10), 0);",
        "  assert.equal(clamp(11, 0, 10), 10);",
        "});",
        "",
        "test(\"clamp rejects inverted ranges\", () => {",
        "  assert.throws(() => clamp(1, 10, 0), /min.*max/i);",
        "});",
        "",
      ].join("\n"),
    },
  },
];

const GOLDEN_TASKS_BY_ID = new Map(GOLDEN_TASKS.map((task) => [task.id, task]));

function goldenTaskInfo(task: CodingGoldenTask): CodingGoldenTaskInfo {
  return {
    id: task.id,
    title: task.title,
    category: task.category,
    prompt: task.prompt,
    verificationCommands: task.verificationCommands,
    successCriteria: task.successCriteria,
  };
}

async function startGoldenRun(
  workspaceRoot: string,
  input: CodingBenchmarkInput,
): Promise<CodingGoldenRun> {
  const suite = input.suite ?? GOLDEN_SUITE;
  if (suite !== GOLDEN_SUITE) {
    throw new Error(`unsupported coding benchmark suite: ${suite}`);
  }
  const runId = normalizeRunId(input.runId ?? `run-${Date.now().toString(36)}`);
  if (runId === null) {
    throw new Error("runId may only contain letters, numbers, dots, underscores, and dashes");
  }
  const requestedTaskIds = input.taskIds ?? GOLDEN_TASKS.map((task) => task.id);
  const tasks = requestedTaskIds.map((taskId) => {
    const task = GOLDEN_TASKS_BY_ID.get(taskId);
    if (!task) throw new Error(`unknown golden benchmark task: ${taskId}`);
    return task;
  });
  const runRoot = path.join(goldenRootPath(workspaceRoot), runId);
  if (await exists(runRoot)) {
    throw new Error(`golden benchmark run already exists: ${runId}`);
  }

  const outputTasks: CodingGoldenRunTask[] = [];
  for (const task of tasks) {
    const workspacePath = path.join(runRoot, task.id, "workspace");
    await writeTaskWorkspace(workspacePath, task);
    outputTasks.push({
      id: task.id,
      title: task.title,
      category: task.category,
      workspacePath: relativeStorePath(workspaceRoot, workspacePath),
      prompt: task.prompt,
      verificationCommands: task.verificationCommands,
      successCriteria: task.successCriteria,
    });
  }

  const run: CodingGoldenRun = {
    runId,
    suite,
    path: relativeStorePath(workspaceRoot, runRoot),
    taskCount: outputTasks.length,
    tasks: outputTasks,
  };
  await fs.writeFile(
    path.join(runRoot, "manifest.json"),
    `${JSON.stringify({ ...run, createdAt: new Date().toISOString() }, null, 2)}\n`,
    "utf8",
  );
  return run;
}

async function writeTaskWorkspace(workspacePath: string, task: CodingGoldenTask): Promise<void> {
  for (const [relPath, content] of Object.entries(task.files)) {
    const filePath = path.join(workspacePath, relPath);
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, content, "utf8");
  }
}

async function exists(targetPath: string): Promise<boolean> {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

function goldenRootPath(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".magi", "coding-benchmark-golden");
}

function normalizeRunId(value: string): string | null {
  const trimmed = value.trim();
  if (!/^[A-Za-z0-9._-]+$/.test(trimmed)) return null;
  return trimmed;
}

function benchmarkStorePath(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".magi", "coding-benchmark-runs.jsonl");
}

function benchmarkReportDirPath(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".magi", "coding-benchmark-reports");
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

async function writeBenchmarkReport(
  workspaceRoot: string,
  records: readonly CodingBenchmarkRecord[],
  summary: CodingBenchmarkSummary,
): Promise<CodingBenchmarkReport> {
  const goldenRuns = await summarizeGoldenRuns(workspaceRoot, records);
  const reportDir = benchmarkReportDirPath(workspaceRoot);
  await fs.mkdir(reportDir, { recursive: true });

  const jsonPath = path.join(reportDir, "latest.json");
  const markdownPath = path.join(reportDir, "latest.md");
  const report: CodingBenchmarkReport = {
    generatedAt: new Date().toISOString(),
    jsonPath: relativeStorePath(workspaceRoot, jsonPath),
    markdownPath: relativeStorePath(workspaceRoot, markdownPath),
    summary,
    byCategory: summarizeByCategory(records),
    byTask: summarizeByTask(records),
    goldenRuns,
  };
  await fs.writeFile(jsonPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  await fs.writeFile(markdownPath, renderBenchmarkMarkdown(report), "utf8");
  return report;
}

function summarizeByCategory(
  records: readonly CodingBenchmarkRecord[],
): CodingBenchmarkCategoryReport[] {
  const groups = groupRecords(records, (record) => record.category);
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([category, categoryRecords]) => ({
      category,
      ...summarizeReportGroup(categoryRecords),
    }));
}

function summarizeByTask(records: readonly CodingBenchmarkRecord[]): CodingBenchmarkTaskReport[] {
  const groups = groupRecords(records, (record) => `${record.runId ?? ""}\0${record.taskId}`);
  return [...groups.values()]
    .sort((left, right) => {
      const leftTask = left[0]?.taskId ?? "";
      const rightTask = right[0]?.taskId ?? "";
      const taskSort = leftTask.localeCompare(rightTask);
      if (taskSort !== 0) return taskSort;
      const leftRun = left[0]?.runId ?? "";
      const rightRun = right[0]?.runId ?? "";
      return leftRun.localeCompare(rightRun);
    })
    .map((taskRecords) => {
      const first = taskRecords[0]!;
      return {
        taskId: first.taskId,
        category: first.category,
        ...(first.runId ? { runId: first.runId } : {}),
        ...summarizeReportGroup(taskRecords),
      };
    });
}

function summarizeReportGroup(
  records: readonly CodingBenchmarkRecord[],
): CodingBenchmarkReportGroup {
  const summary = summarize(records);
  const testsPassed = records.filter((record) => record.testsPassed).length;
  return {
    ...summary,
    testsPassRate: records.length === 0 ? 0 : testsPassed / records.length,
  };
}

async function summarizeGoldenRuns(
  workspaceRoot: string,
  records: readonly CodingBenchmarkRecord[],
): Promise<CodingBenchmarkGoldenRunReport[]> {
  const manifests = await readGoldenRunManifests(workspaceRoot);
  return manifests.map((run) => {
    const runRecords = records.filter((record) => record.runId === run.runId);
    const runSummary = summarize(runRecords);
    return {
      runId: run.runId,
      taskCount: run.taskCount,
      recordedRuns: runSummary.totalRuns,
      passedRuns: runSummary.passedRuns,
      failedRuns: runSummary.failedRuns,
      blockedRuns: runSummary.blockedRuns,
      successRate: runSummary.successRate,
    };
  });
}

async function readGoldenRunManifests(workspaceRoot: string): Promise<CodingGoldenRun[]> {
  const root = goldenRootPath(workspaceRoot);
  let entries: string[];
  try {
    entries = await fs.readdir(root);
  } catch {
    return [];
  }

  const runs: CodingGoldenRun[] = [];
  for (const entry of entries.sort()) {
    const manifestPath = path.join(root, entry, "manifest.json");
    try {
      const parsed = JSON.parse(await fs.readFile(manifestPath, "utf8")) as Partial<CodingGoldenRun>;
      if (
        parsed.runId &&
        parsed.suite === GOLDEN_SUITE &&
        typeof parsed.path === "string" &&
        typeof parsed.taskCount === "number" &&
        Array.isArray(parsed.tasks)
      ) {
        runs.push({
          runId: parsed.runId,
          suite: parsed.suite,
          path: parsed.path,
          taskCount: parsed.taskCount,
          tasks: parsed.tasks as CodingGoldenRunTask[],
        });
      }
    } catch {
      /* ignore malformed historical manifests */
    }
  }
  return runs;
}

function groupRecords(
  records: readonly CodingBenchmarkRecord[],
  keyFor: (record: CodingBenchmarkRecord) => string,
): Map<string, CodingBenchmarkRecord[]> {
  const groups = new Map<string, CodingBenchmarkRecord[]>();
  for (const record of records) {
    const key = keyFor(record);
    const group = groups.get(key);
    if (group) {
      group.push(record);
    } else {
      groups.set(key, [record]);
    }
  }
  return groups;
}

function renderBenchmarkMarkdown(report: CodingBenchmarkReport): string {
  return [
    "# Coding Benchmark Report",
    "",
    `Generated: ${report.generatedAt}`,
    "",
    "## Summary",
    "",
    "| Metric | Value |",
    "| --- | ---: |",
    `| Total runs | ${report.summary.totalRuns} |`,
    `| Passed | ${report.summary.passedRuns} |`,
    `| Failed | ${report.summary.failedRuns} |`,
    `| Blocked | ${report.summary.blockedRuns} |`,
    `| Success rate | ${formatPercent(report.summary.successRate)} |`,
    `| Average retry count | ${formatNumber(report.summary.averageRetryCount)} |`,
    `| Wrong completion claim rate | ${formatNumber(report.summary.wrongCompletionClaimRate)} |`,
    "",
    "## Categories",
    "",
    "| Category | Runs | Passed | Failed | Blocked | Success | Tests |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...report.byCategory.map(
      (category) =>
        `| ${category.category} | ${category.totalRuns} | ${category.passedRuns} | ${category.failedRuns} | ${category.blockedRuns} | ${formatPercent(category.successRate)} | ${formatPercent(category.testsPassRate)} |`,
    ),
    "",
    "## Tasks",
    "",
    "| Task | Run | Category | Runs | Success | Tests | Avg retries | Wrong claims |",
    "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ...report.byTask.map(
      (task) =>
        `| ${task.taskId} | ${task.runId ?? "-"} | ${task.category} | ${task.totalRuns} | ${formatPercent(task.successRate)} | ${formatPercent(task.testsPassRate)} | ${formatNumber(task.averageRetryCount)} | ${formatNumber(task.wrongCompletionClaimRate)} |`,
    ),
    "",
    "## Golden Runs",
    "",
    "| Run | Tasks | Recorded | Passed | Failed | Blocked | Success |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...report.goldenRuns.map(
      (run) =>
        `| ${run.runId} | ${run.taskCount} | ${run.recordedRuns} | ${run.passedRuns} | ${run.failedRuns} | ${run.blockedRuns} | ${formatPercent(run.successRate)} |`,
    ),
    "",
  ].join("\n");
}

function formatPercent(value: number): string {
  return `${formatNumber(value * 100)}%`;
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}
