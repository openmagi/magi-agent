import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { test } from "vitest";

import {
  evaluateResearchRun,
  loadJsonFile,
  normalizeScore,
} from "./research-parity-eval.mjs";

test("normalizeScore clamps numeric rubric scores to 0..5", () => {
  assert.equal(normalizeScore(-1), 0);
  assert.equal(normalizeScore(2.25), 2.25);
  assert.equal(normalizeScore(9), 5);
});

test("evaluateResearchRun computes weighted aggregate and sorted failures", async () => {
  const benchmark = await loadJsonFile("docs/notes/research-parity/benchmark-v1.json");
  const run = await loadJsonFile("docs/notes/research-parity/sample-run.json");

  const report = evaluateResearchRun(benchmark, run);

  assert.equal(report.ok, true);
  assert.equal(report.taskCount, benchmark.tasks.length);
  assert.equal(report.evaluatedCount, 1);
  assert.equal(report.missingTaskIds.length, benchmark.tasks.length - 1);
  assert.equal(report.failureCategories[0], "claim_evidence_partial");
  assert.ok(report.aggregateScore > 0);
  assert.ok(report.aggregateScore <= 5);
});

test("evaluateResearchRun rejects unknown task ids", async () => {
  const benchmark = await loadJsonFile("docs/notes/research-parity/benchmark-v1.json");
  const run = {
    benchmarkVersion: 1,
    agent: "test",
    runId: "bad-task",
    createdAt: "2026-05-08T00:00:00.000Z",
    results: [{ taskId: "missing-task", scores: {}, failureCategories: [] }],
  };

  assert.throws(
    () => evaluateResearchRun(benchmark, run),
    /unknown task id: missing-task/,
  );
});

test("evaluateResearchRun reports threshold status from aggregate score", async () => {
  const benchmark = await loadJsonFile("docs/notes/research-parity/benchmark-v1.json");
  const run = await loadJsonFile("docs/notes/research-parity/sample-run.json");

  const report = evaluateResearchRun(benchmark, {
    ...run,
    results: run.results.map((result) => ({
      ...result,
      scores: Object.fromEntries(benchmark.rubric.map((key) => [key, 1])),
    })),
  });

  assert.equal(report.threshold, "needs_work");
});

test("CLI writes a normalized report", async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "research-parity-"));
  const outPath = path.join(tmp, "report.json");
  try {
    const result = await runScript(process.execPath, [
      "scripts/research-parity-eval.mjs",
      "--benchmark",
      "docs/notes/research-parity/benchmark-v1.json",
      "--run",
      "docs/notes/research-parity/sample-run.json",
      "--out",
      outPath,
    ]);

    assert.equal(result.status, 0, result.stderr);
    const written = JSON.parse(await fs.readFile(outPath, "utf8"));
    assert.equal(written.ok, true);
    assert.equal(written.agent, "magi-agent");
  } finally {
    await fs.rm(tmp, { recursive: true, force: true });
  }
});

function runScript(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: process.cwd() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("close", (status) => {
      resolve({ status, stdout, stderr });
    });
  });
}
