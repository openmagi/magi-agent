import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("agent-run.sh default execution context", () => {
  const body = readFileSync(
    path.resolve("src/lib/templates/scripts/agent-run.sh"),
    "utf8",
  );

  it("injects baseline execution docs even when caller omits --context", () => {
    expect(body).toContain("DEFAULT_CONTEXT_FILE");
    expect(body).toContain("SUBAGENT_BASELINE_FILES");
    expect(body).toContain("CLAUDE.md");
    expect(body).toContain("AGENTS.md");
    expect(body).toContain("MEMORY.md");
    expect(body).toContain("memory/ROOT.md");
    expect(body).toContain("LEARNING.md");
    expect(body).toContain("TOOLS.md");
    expect(body).toContain("EXECUTION.md");
    expect(body).toContain("DISCIPLINE.md");
    expect(body).toContain("EXECUTION-TOOLS.md");
    expect(body).toContain("Subagent Execution Baseline");
    expect(body).toContain("parent meta-layer sections are parent-only");
    expect(body).toContain("renderAgentsForChild");
    expect(body).toContain("renderExecutionForChild");
    expect(body).toContain("renderExecutionToolsForChild");
    expect(body).not.toContain("SUBAGENT_BASELINE_FILES=\"SOUL.md");
  });

  it("appends caller context instead of replacing the baseline", () => {
    expect(body).toContain("Caller Context");
    expect(body).toContain("cat \"$CONTEXT_FILE\" >> \"$WORKDIR/CONTEXT.md\"");
  });

  it("forces shell-launched subagents to read the synthesized context first", () => {
    expect(body).toContain("RUN_PROMPT");
    expect(body).toContain("Read and follow ./CONTEXT.md before acting");
    expect(body).toContain("$CLAUDE_BIN -p \"$RUN_PROMPT\"");
  });
});
