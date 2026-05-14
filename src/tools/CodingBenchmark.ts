import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";

export type CodingBenchmarkOutcome = "passed" | "failed" | "blocked";
export type CodingBenchmarkSuite = "coding-golden-v1";
export type CodingBenchmarkLanguage = "javascript" | "python";
export type CodingBenchmarkHarnessCapability =
  | "child_worktree_adoption"
  | "child_worktree_conflict_disposition"
  | "tournament_worktree";

export interface CodingBenchmarkInput {
  action: "record" | "summary" | "list_tasks" | "start_run" | "report";
  suite?: CodingBenchmarkSuite;
  runId?: string;
  taskIds?: string[];
  languages?: CodingBenchmarkLanguage[];
  categories?: string[];
  harnessCapabilities?: CodingBenchmarkHarnessCapability[];
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
  language: CodingBenchmarkLanguage;
  category: string;
  harnessCapabilities?: CodingBenchmarkHarnessCapability[];
  prompt: string;
  verificationCommands: string[];
  successCriteria: string[];
  files: Record<string, string>;
}

export interface CodingGoldenTaskInfo {
  id: string;
  title: string;
  language: CodingBenchmarkLanguage;
  category: string;
  harnessCapabilities: CodingBenchmarkHarnessCapability[];
  prompt: string;
  verificationCommands: string[];
  successCriteria: string[];
}

export interface CodingGoldenRunTask {
  id: string;
  title: string;
  language: CodingBenchmarkLanguage;
  category: string;
  harnessCapabilities: CodingBenchmarkHarnessCapability[];
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

export interface CodingBenchmarkLanguageReport extends CodingBenchmarkReportGroup {
  language: string;
}

export interface CodingBenchmarkCategoryReport extends CodingBenchmarkReportGroup {
  category: string;
}

export interface CodingBenchmarkHarnessCapabilityReport extends CodingBenchmarkReportGroup {
  harnessCapability: string;
}

export interface CodingBenchmarkTaskReport extends CodingBenchmarkReportGroup {
  taskId: string;
  language: string;
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
  byLanguage: CodingBenchmarkLanguageReport[];
  byHarnessCapability: CodingBenchmarkHarnessCapabilityReport[];
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
    languages: {
      type: "array",
      items: { type: "string", enum: ["javascript", "python"] },
    },
    categories: { type: "array", items: { type: "string" } },
    harnessCapabilities: {
      type: "array",
      items: {
        type: "string",
        enum: [
          "child_worktree_adoption",
          "child_worktree_conflict_disposition",
          "tournament_worktree",
        ],
      },
    },
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
    shouldDefer: true,
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
      if (input.action === "list_tasks" || input.action === "start_run") {
        if (input.languages !== undefined) {
          if (!Array.isArray(input.languages) || input.languages.length === 0) {
            return "`languages` must be a non-empty array when provided";
          }
          const invalid = input.languages.find(isUnknownGoldenTaskLanguage);
          if (invalid) return `unknown golden benchmark language: ${invalid}`;
        }
        if (input.categories !== undefined) {
          if (!Array.isArray(input.categories) || input.categories.length === 0) {
            return "`categories` must be a non-empty string array when provided";
          }
          if (input.categories.some((category) => typeof category !== "string" || !category.trim())) {
            return "`categories` must contain non-empty strings";
          }
        }
        if (input.harnessCapabilities !== undefined) {
          if (!Array.isArray(input.harnessCapabilities) || input.harnessCapabilities.length === 0) {
            return "`harnessCapabilities` must be a non-empty array when provided";
          }
          const invalid = input.harnessCapabilities.find(isUnknownHarnessCapability);
          if (invalid) return `unknown coding benchmark harness capability: ${invalid}`;
        }
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
          goldenTasks = selectGoldenTasks(input).map(goldenTaskInfo);
        }
        if (input.action === "start_run") {
          goldenRun = await startGoldenRun(workspaceRoot, input);
          goldenTasks = goldenRun.tasks.map((task) => ({
            id: task.id,
            title: task.title,
            language: task.language,
            category: task.category,
            harnessCapabilities: task.harnessCapabilities,
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
    language: "javascript",
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
    language: "javascript",
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
  {
    id: "js-multifile-cart-total",
    title: "Fix a multi-file cart total regression",
    language: "javascript",
    category: "multifile",
    prompt:
      "Fix the cart total calculation across the cart and pricing modules. Preserve the public calculateCartTotal API and make the supplied tests pass.",
    verificationCommands: ["npm test"],
    successCriteria: [
      "`npm test` passes in the task workspace",
      "Percentage discounts are applied to the subtotal instead of subtracted as raw currency",
      "Money totals are rounded to two decimal places",
    ],
    files: {
      "package.json": `${JSON.stringify(
        {
          type: "module",
          scripts: { test: "node --test test/cart.test.js" },
        },
        null,
        2,
      )}\n`,
      "src/pricing.js": [
        "export function applyDiscount(subtotal, discountPercent) {",
        "  return subtotal - discountPercent;",
        "}",
        "",
        "export function roundMoney(value) {",
        "  return Math.round(value * 100) / 100;",
        "}",
        "",
      ].join("\n"),
      "src/cart.js": [
        "import { applyDiscount, roundMoney } from \"./pricing.js\";",
        "",
        "export function calculateCartTotal(items, discountPercent = 0) {",
        "  const subtotal = items.reduce(",
        "    (sum, item) => sum + item.price * item.quantity,",
        "    0,",
        "  );",
        "  return roundMoney(applyDiscount(subtotal, discountPercent));",
        "}",
        "",
      ].join("\n"),
      "test/cart.test.js": [
        "import assert from \"node:assert/strict\";",
        "import test from \"node:test\";",
        "import { calculateCartTotal } from \"../src/cart.js\";",
        "",
        "test(\"calculates cart totals with percentage discounts\", () => {",
        "  const items = [",
        "    { price: 12.5, quantity: 2 },",
        "    { price: 5, quantity: 1 },",
        "  ];",
        "  assert.equal(calculateCartTotal(items, 10), 27);",
        "});",
        "",
        "test(\"rounds floating point totals to cents\", () => {",
        "  assert.equal(calculateCartTotal([{ price: 0.1, quantity: 3 }]), 0.3);",
        "});",
        "",
        "test(\"keeps empty carts at zero\", () => {",
        "  assert.equal(calculateCartTotal([], 25), 0);",
        "});",
        "",
      ].join("\n"),
    },
  },
  {
    id: "js-build-missing-export",
    title: "Restore a broken public API export",
    language: "javascript",
    category: "build_fix",
    prompt:
      "Fix the public API module so consumers can import parseUserId from src/index.js. Do not remove or rename the existing formatUserName export.",
    verificationCommands: ["npm test"],
    successCriteria: [
      "`npm test` passes in the task workspace",
      "src/index.js continues to export formatUserName",
      "parseUserId parses user_<number> identifiers and rejects invalid IDs",
    ],
    files: {
      "package.json": `${JSON.stringify(
        {
          type: "module",
          scripts: { test: "node --test test/public-api.test.js" },
        },
        null,
        2,
      )}\n`,
      "src/index.js": [
        "export { formatUserName, parseUserId } from \"./user.js\";",
        "",
      ].join("\n"),
      "src/user.js": [
        "export function formatUserName(name) {",
        "  return name.trim().replace(/\\s+/g, \" \");",
        "}",
        "",
      ].join("\n"),
      "test/public-api.test.js": [
        "import assert from \"node:assert/strict\";",
        "import test from \"node:test\";",
        "import { formatUserName, parseUserId } from \"../src/index.js\";",
        "",
        "test(\"keeps existing name formatting export\", () => {",
        "  assert.equal(formatUserName(\"  Ada   Lovelace  \"), \"Ada Lovelace\");",
        "});",
        "",
        "test(\"exports parseUserId from the public API\", () => {",
        "  assert.equal(parseUserId(\"user_42\"), 42);",
        "  assert.throws(() => parseUserId(\"account_42\"), /invalid/i);",
        "});",
        "",
      ].join("\n"),
    },
  },
  {
    id: "js-security-path-sandbox",
    title: "Harden workspace path validation",
    language: "javascript",
    category: "security",
    prompt:
      "Harden isSafeWorkspacePath so workspace-relative paths cannot escape the workspace. Preserve the function name and avoid accepting absolute paths.",
    verificationCommands: ["npm test"],
    successCriteria: [
      "`npm test` passes in the task workspace",
      "Relative paths inside the workspace are accepted",
      "Parent traversal and absolute paths are rejected",
    ],
    files: {
      "package.json": `${JSON.stringify(
        {
          type: "module",
          scripts: { test: "node --test test/pathPolicy.test.js" },
        },
        null,
        2,
      )}\n`,
      "src/pathPolicy.js": [
        "export function isSafeWorkspacePath(relativePath) {",
        "  if (relativePath.startsWith(\"../\")) return false;",
        "  return !relativePath.includes(\"\\0\");",
        "}",
        "",
      ].join("\n"),
      "test/pathPolicy.test.js": [
        "import assert from \"node:assert/strict\";",
        "import test from \"node:test\";",
        "import { isSafeWorkspacePath } from \"../src/pathPolicy.js\";",
        "",
        "test(\"accepts ordinary workspace-relative paths\", () => {",
        "  assert.equal(isSafeWorkspacePath(\"docs/readme.md\"), true);",
        "  assert.equal(isSafeWorkspacePath(\"src/lib/index.js\"), true);",
        "});",
        "",
        "test(\"rejects traversal and absolute paths\", () => {",
        "  assert.equal(isSafeWorkspacePath(\"../secret.txt\"), false);",
        "  assert.equal(isSafeWorkspacePath(\"docs/../../secret.txt\"), false);",
        "  assert.equal(isSafeWorkspacePath(\"/tmp/secret.txt\"), false);",
        "  assert.equal(isSafeWorkspacePath(\"docs\\\\..\\\\secret.txt\"), false);",
        "});",
        "",
      ].join("\n"),
    },
  },
  {
    id: "js-async-retry",
    title: "Implement bounded async retry",
    language: "javascript",
    category: "async",
    prompt:
      "Implement the retry(operation, options) helper so transient async failures are retried up to maxAttempts. Preserve the exported function name.",
    verificationCommands: ["npm test"],
    successCriteria: [
      "`npm test` passes in the task workspace",
      "retry resolves with the first successful attempt",
      "retry rejects with the final error after maxAttempts is exhausted",
    ],
    files: {
      "package.json": `${JSON.stringify(
        {
          type: "module",
          scripts: { test: "node --test test/retry.test.js" },
        },
        null,
        2,
      )}\n`,
      "src/retry.js": [
        "export async function retry(operation, _options = {}) {",
        "  return operation();",
        "}",
        "",
      ].join("\n"),
      "test/retry.test.js": [
        "import assert from \"node:assert/strict\";",
        "import test from \"node:test\";",
        "import { retry } from \"../src/retry.js\";",
        "",
        "test(\"retries until an async operation succeeds\", async () => {",
        "  let attempts = 0;",
        "  const result = await retry(async () => {",
        "    attempts += 1;",
        "    if (attempts < 3) throw new Error(\"try again\");",
        "    return \"ok\";",
        "  });",
        "  assert.equal(result, \"ok\");",
        "  assert.equal(attempts, 3);",
        "});",
        "",
        "test(\"rejects with the final error after maxAttempts\", async () => {",
        "  let attempts = 0;",
        "  await assert.rejects(",
        "    retry(async () => {",
        "      attempts += 1;",
        "      throw new Error(`failure ${attempts}`);",
        "    }, { maxAttempts: 2 }),",
        "    /failure 2/,",
        "  );",
        "  assert.equal(attempts, 2);",
        "});",
        "",
      ].join("\n"),
    },
  },
  {
    id: "py-bugfix-slugify",
    title: "Fix Python slug normalization",
    language: "python",
    category: "bugfix",
    prompt:
      "Fix slugify(text) so it produces stable URL slugs. Preserve the public function name and make the supplied unittest suite pass.",
    verificationCommands: ["python3 -m unittest discover -s tests"],
    successCriteria: [
      "`python3 -m unittest discover -s tests` passes in the task workspace",
      "Slug output is lowercase, punctuation-free, and hyphen separated",
      "Repeated whitespace or punctuation does not create duplicate hyphens",
    ],
    files: {
      "app/__init__.py": "",
      "app/text_utils.py": [
        "def slugify(text):",
        "    return text.lower().replace(\" \", \"-\")",
        "",
      ].join("\n"),
      "tests/test_text_utils.py": [
        "import unittest",
        "",
        "from app.text_utils import slugify",
        "",
        "",
        "class SlugifyTests(unittest.TestCase):",
        "    def test_normalizes_punctuation_and_spacing(self):",
        "        self.assertEqual(slugify(\"  Hello,   World!  \"), \"hello-world\")",
        "",
        "    def test_keeps_numbers_without_punctuation(self):",
        "        self.assertEqual(slugify(\"Release v2.0\"), \"release-v20\")",
        "",
        "    def test_empty_input_returns_empty_slug(self):",
        "        self.assertEqual(slugify(\" !!! \"), \"\")",
        "",
        "",
        "if __name__ == \"__main__\":",
        "    unittest.main()",
        "",
      ].join("\n"),
    },
  },
  {
    id: "py-feature-windowed-average",
    title: "Implement Python windowed averages",
    language: "python",
    category: "feature",
    prompt:
      "Implement windowed_average(values, window_size) using only the Python standard library. Preserve the public function name and make the supplied unittest suite pass.",
    verificationCommands: ["python3 -m unittest discover -s tests"],
    successCriteria: [
      "`python3 -m unittest discover -s tests` passes in the task workspace",
      "Averages are computed for each contiguous fixed-size window",
      "Invalid window sizes raise ValueError",
    ],
    files: {
      "app/__init__.py": "",
      "app/metrics.py": [
        "def windowed_average(_values, _window_size):",
        "    raise NotImplementedError(\"windowed_average is not implemented\")",
        "",
      ].join("\n"),
      "tests/test_metrics.py": [
        "import unittest",
        "",
        "from app.metrics import windowed_average",
        "",
        "",
        "class WindowedAverageTests(unittest.TestCase):",
        "    def test_computes_contiguous_window_averages(self):",
        "        self.assertEqual(windowed_average([2, 4, 6, 8], 2), [3, 5, 7])",
        "        self.assertEqual(windowed_average([1, 2, 3, 4, 5], 3), [2, 3, 4])",
        "",
        "    def test_returns_empty_list_when_window_is_larger_than_values(self):",
        "        self.assertEqual(windowed_average([10, 20], 3), [])",
        "",
        "    def test_rejects_invalid_window_sizes(self):",
        "        with self.assertRaisesRegex(ValueError, \"window\"):",
        "            windowed_average([1, 2, 3], 0)",
        "",
        "",
        "if __name__ == \"__main__\":",
        "    unittest.main()",
        "",
      ].join("\n"),
    },
  },
  {
    id: "py-security-redact-secrets",
    title: "Redact Python log secrets",
    language: "python",
    category: "security",
    prompt:
      "Harden redact_secrets(text) so logs do not expose common API keys or bearer tokens. Preserve labels and make the supplied unittest suite pass.",
    verificationCommands: ["python3 -m unittest discover -s tests"],
    successCriteria: [
      "`python3 -m unittest discover -s tests` passes in the task workspace",
      "API key and token values are replaced with [REDACTED]",
      "Non-secret log text is preserved",
    ],
    files: {
      "app/__init__.py": "",
      "app/redact.py": [
        "def redact_secrets(text):",
        "    return text.replace(\"api_key\", \"[REDACTED]\")",
        "",
      ].join("\n"),
      "tests/test_redact.py": [
        "import unittest",
        "",
        "from app.redact import redact_secrets",
        "",
        "",
        "class RedactSecretsTests(unittest.TestCase):",
        "    def test_redacts_api_key_and_token_values(self):",
        "        raw = \"POST /sync api_key=alpha123 token: beta456 ok\"",
        "        self.assertEqual(",
        "            redact_secrets(raw),",
        "            \"POST /sync api_key=[REDACTED] token: [REDACTED] ok\",",
        "        )",
        "",
        "    def test_redacts_bearer_tokens_case_insensitively(self):",
        "        self.assertEqual(",
        "            redact_secrets(\"Authorization: Bearer abc.def.ghi\"),",
        "            \"Authorization: Bearer [REDACTED]\",",
        "        )",
        "",
        "    def test_preserves_non_secret_text(self):",
        "        self.assertEqual(redact_secrets(\"status=ok user_id=42\"), \"status=ok user_id=42\")",
        "",
        "",
        "if __name__ == \"__main__\":",
        "    unittest.main()",
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
    language: task.language,
    category: task.category,
    harnessCapabilities: task.harnessCapabilities ?? [],
    prompt: task.prompt,
    verificationCommands: task.verificationCommands,
    successCriteria: task.successCriteria,
  };
}

function isUnknownGoldenTaskLanguage(value: string): boolean {
  return value !== "javascript" && value !== "python";
}

function isUnknownHarnessCapability(value: string): boolean {
  return (
    value !== "child_worktree_adoption" &&
    value !== "child_worktree_conflict_disposition" &&
    value !== "tournament_worktree"
  );
}

function selectGoldenTasks(input: CodingBenchmarkInput): CodingGoldenTask[] {
  const requestedTaskIds = input.taskIds ?? GOLDEN_TASKS.map((task) => task.id);
  const selected = requestedTaskIds.map((taskId) => {
    const task = GOLDEN_TASKS_BY_ID.get(taskId);
    if (!task) throw new Error(`unknown golden benchmark task: ${taskId}`);
    return task;
  });
  const languages = new Set(input.languages);
  const categories = new Set(input.categories?.map((category) => category.trim()));
  const harnessCapabilities = new Set(input.harnessCapabilities);
  return selected.filter((task) => {
    if (languages.size > 0 && !languages.has(task.language)) return false;
    if (categories.size > 0 && !categories.has(task.category)) return false;
    if (
      harnessCapabilities.size > 0 &&
      !(task.harnessCapabilities ?? []).some((capability) => harnessCapabilities.has(capability))
    ) {
      return false;
    }
    return true;
  });
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
  const tasks = selectGoldenTasks(input);
  if (tasks.length === 0) {
    throw new Error("no golden benchmark tasks match the requested filters");
  }
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
      language: task.language,
      category: task.category,
      harnessCapabilities: task.harnessCapabilities ?? [],
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
  const manifests = await readGoldenRunManifests(workspaceRoot);
  const taskLanguages = buildTaskLanguageMap(manifests);
  const taskHarnessCapabilities = buildTaskHarnessCapabilityMap(manifests);
  const goldenRuns = summarizeGoldenRuns(manifests, records);
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
    byLanguage: summarizeByLanguage(records, taskLanguages),
    byHarnessCapability: summarizeByHarnessCapability(records, taskHarnessCapabilities),
    byTask: summarizeByTask(records, taskLanguages),
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

function summarizeByLanguage(
  records: readonly CodingBenchmarkRecord[],
  taskLanguages: ReadonlyMap<string, string>,
): CodingBenchmarkLanguageReport[] {
  const groups = groupRecords(records, (record) => languageForRecord(record, taskLanguages));
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([language, languageRecords]) => ({
      language,
      ...summarizeReportGroup(languageRecords),
    }));
}

function summarizeByHarnessCapability(
  records: readonly CodingBenchmarkRecord[],
  taskHarnessCapabilities: ReadonlyMap<string, readonly string[]>,
): CodingBenchmarkHarnessCapabilityReport[] {
  const groups = new Map<string, CodingBenchmarkRecord[]>();
  for (const record of records) {
    for (const capability of harnessCapabilitiesForRecord(record, taskHarnessCapabilities)) {
      const group = groups.get(capability);
      if (group) {
        group.push(record);
      } else {
        groups.set(capability, [record]);
      }
    }
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([harnessCapability, capabilityRecords]) => ({
      harnessCapability,
      ...summarizeReportGroup(capabilityRecords),
    }));
}

function summarizeByTask(
  records: readonly CodingBenchmarkRecord[],
  taskLanguages: ReadonlyMap<string, string>,
): CodingBenchmarkTaskReport[] {
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
        language: languageForRecord(first, taskLanguages),
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

function summarizeGoldenRuns(
  manifests: readonly CodingGoldenRun[],
  records: readonly CodingBenchmarkRecord[],
): CodingBenchmarkGoldenRunReport[] {
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

function buildTaskLanguageMap(manifests: readonly CodingGoldenRun[]): Map<string, string> {
  const languages = new Map<string, string>();
  for (const task of GOLDEN_TASKS) {
    languages.set(task.id, task.language);
  }
  for (const run of manifests) {
    for (const task of run.tasks) {
      languages.set(`${run.runId}\0${task.id}`, task.language);
      languages.set(task.id, task.language);
    }
  }
  return languages;
}

function languageForRecord(
  record: CodingBenchmarkRecord,
  taskLanguages: ReadonlyMap<string, string>,
): string {
  if (record.runId) {
    const runScopedLanguage = taskLanguages.get(`${record.runId}\0${record.taskId}`);
    if (runScopedLanguage) return runScopedLanguage;
  }
  return taskLanguages.get(record.taskId) ?? "unknown";
}

function buildTaskHarnessCapabilityMap(
  manifests: readonly CodingGoldenRun[],
): Map<string, readonly string[]> {
  const capabilities = new Map<string, readonly string[]>();
  for (const task of GOLDEN_TASKS) {
    capabilities.set(task.id, task.harnessCapabilities ?? []);
  }
  for (const run of manifests) {
    for (const task of run.tasks) {
      const taskCapabilities = task.harnessCapabilities ?? [];
      capabilities.set(`${run.runId}\0${task.id}`, taskCapabilities);
      capabilities.set(task.id, taskCapabilities);
    }
  }
  return capabilities;
}

function harnessCapabilitiesForRecord(
  record: CodingBenchmarkRecord,
  taskHarnessCapabilities: ReadonlyMap<string, readonly string[]>,
): readonly string[] {
  if (record.runId) {
    const runScopedCapabilities = taskHarnessCapabilities.get(`${record.runId}\0${record.taskId}`);
    if (runScopedCapabilities) return runScopedCapabilities;
  }
  return taskHarnessCapabilities.get(record.taskId) ?? [];
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
    "## Languages",
    "",
    "| Language | Runs | Passed | Failed | Blocked | Success | Tests |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...report.byLanguage.map(
      (language) =>
        `| ${language.language} | ${language.totalRuns} | ${language.passedRuns} | ${language.failedRuns} | ${language.blockedRuns} | ${formatPercent(language.successRate)} | ${formatPercent(language.testsPassRate)} |`,
    ),
    "",
    "## Harness Capabilities",
    "",
    "| Harness Capability | Runs | Passed | Failed | Blocked | Success | Tests |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...report.byHarnessCapability.map(
      (capability) =>
        `| ${capability.harnessCapability} | ${capability.totalRuns} | ${capability.passedRuns} | ${capability.failedRuns} | ${capability.blockedRuns} | ${formatPercent(capability.successRate)} | ${formatPercent(capability.testsPassRate)} |`,
    ),
    "",
    "## Tasks",
    "",
    "| Task | Run | Language | Category | Runs | Success | Tests | Avg retries | Wrong claims |",
    "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ...report.byTask.map(
      (task) =>
        `| ${task.taskId} | ${task.runId ?? "-"} | ${task.language} | ${task.category} | ${task.totalRuns} | ${formatPercent(task.successRate)} | ${formatPercent(task.testsPassRate)} | ${formatNumber(task.averageRetryCount)} | ${formatNumber(task.wrongCompletionClaimRate)} |`,
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
