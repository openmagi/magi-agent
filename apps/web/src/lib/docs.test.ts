import { describe, expect, it } from "vitest";
import { DOCS_PAGES, buildLlmsFullText, buildLlmsText } from "./docs";

describe("Open Magi docs content", () => {
  it("publishes the canonical multi-page docs map", () => {
    expect(DOCS_PAGES.map((page) => page.href)).toEqual([
      "/docs",
      "/docs/getting-started",
      "/docs/quickstart",
      "/docs/cli",
      "/docs/configuration",
      "/docs/customization",
      "/docs/runtime",
      "/docs/tools",
      "/docs/contracts",
      "/docs/hooks",
      "/docs/memory",
      "/docs/skills",
      "/docs/automation",
      "/docs/integrations",
      "/docs/api",
      "/docs/deployment",
      "/docs/security",
      "/docs/architecture",
      "/docs/reference",
      "/docs/troubleshooting",
    ]);
  });

  it("anchors docs around the canonical open-source repository", () => {
    const sourcePages = DOCS_PAGES.filter((page) =>
      page.sections.some((section) =>
        section.body.some((line) => line.includes("github.com/openmagi/magi-agent")),
      ),
    );

    expect(sourcePages.map((page) => page.href)).toContain("/docs/getting-started");
    expect(buildLlmsText()).toContain("https://github.com/openmagi/magi-agent");
    expect(buildLlmsFullText()).toContain("git clone https://github.com/openmagi/magi-agent.git");
    expect(buildLlmsFullText()).toContain("docker compose up --build");
    expect(buildLlmsFullText()).toContain("npx tsx src/cli/index.ts start");
  });

  it("documents the runtime concepts users need before self-hosting", () => {
    const fullText = buildLlmsFullText();

    expect(fullText).toContain("Execution contracts");
    expect(fullText).toContain("Knowledge Base");
    expect(fullText).toContain("Provider-neutral model routing");
    expect(fullText).toContain("User Harness Rules");
    expect(fullText).toContain("Self-host hardening");
    expect(fullText).toContain("Runtime API boundary");
    expect(fullText).toContain("Cloud boundary");
    expect(fullText).toContain("GET /v1/app/runtime");
  });

  it("documents CLI usage, lifecycle hooks, first-class contracts, and harness customization", () => {
    const fullText = buildLlmsFullText();

    expect(fullText).toContain("CLI command map");
    expect(fullText).toContain("magi-agent init");
    expect(fullText).toContain("magi-agent chat");
    expect(fullText).toContain("magi-agent start");
    expect(fullText).toContain("magi-agent run");
    expect(fullText).toContain("magi-agent serve");
    expect(fullText).toContain("--session");
    expect(fullText).toContain("--model");
    expect(fullText).toContain("--plan");
    expect(fullText).toContain("agent.config.yaml");
    expect(fullText).toContain("harness_rules:");
    expect(fullText).toContain("trigger: beforeCommit");
    expect(fullText).toContain("trigger: afterToolUse");
    expect(fullText).toContain("enforcement: block_on_fail");
    expect(fullText).toContain("enforcement: audit");
    expect(fullText).toContain("HookPoint");
    expect(fullText).toContain("beforeLLMCall");
    expect(fullText).toContain("afterToolUse");
    expect(fullText).toContain("beforeCommit");
    expect(fullText).toContain("runPre");
    expect(fullText).toContain("runPost");
    expect(fullText).toContain("permission_decision");
    expect(fullText).toContain("ExecutionContractStore");
    expect(fullText).toContain("acceptance criteria");
    expect(fullText).toContain("verification evidence");
    expect(fullText).toContain("resource bindings");
    expect(fullText).toContain("Skill runtime hooks");
    expect(fullText).toContain("ChildAgentHarness");
  });

  it("documents Hermes-level operator guides, references, and troubleshooting depth", () => {
    const fullText = buildLlmsFullText();

    expect(fullText).toContain("First task walkthrough");
    expect(fullText).toContain("Tool reference");
    expect(fullText).toContain("FileRead");
    expect(fullText).toContain("FileWrite");
    expect(fullText).toContain("FileEdit");
    expect(fullText).toContain("Bash");
    expect(fullText).toContain("TestRun");
    expect(fullText).toContain("Glob");
    expect(fullText).toContain("Grep");
    expect(fullText).toContain("KnowledgeSearch");
    expect(fullText).toContain("WebSearch");
    expect(fullText).toContain("WebFetch");
    expect(fullText).toContain("Browser");
    expect(fullText).toContain("DocumentWrite");
    expect(fullText).toContain("SpreadsheetWrite");
    expect(fullText).toContain("FileDeliver");
    expect(fullText).toContain("AskUserQuestion");
    expect(fullText).toContain("SpawnAgent");
    expect(fullText).toContain("TaskBoard");
    expect(fullText).toContain("CronCreate");
    expect(fullText).toContain("HookPoint matrix");
    expect(fullText).toContain("beforeTurnStart");
    expect(fullText).toContain("afterTurnEnd");
    expect(fullText).toContain("afterLLMCall");
    expect(fullText).toContain("afterCommit");
    expect(fullText).toContain("onAbort");
    expect(fullText).toContain("onError");
    expect(fullText).toContain("onTaskCheckpoint");
    expect(fullText).toContain("beforeCompaction");
    expect(fullText).toContain("afterCompaction");
    expect(fullText).toContain("onRuleViolation");
    expect(fullText).toContain("onArtifactCreated");
    expect(fullText).toContain("HookResult");
    expect(fullText).toContain("task_contract");
    expect(fullText).toContain("verification_mode");
    expect(fullText).toContain("resource_bindings");
    expect(fullText).toContain("deterministic evidence");
    expect(fullText).toContain("runtime_hooks:");
    expect(fullText).toContain("Hipocampus");
    expect(fullText).toContain("qmd");
    expect(fullText).toContain("compaction");
    expect(fullText).toContain("Goal loop");
    expect(fullText).toContain("Mission");
    expect(fullText).toContain("Cron");
    expect(fullText).toContain("Runtime API");
    expect(fullText).toContain("POST /v1/chat/completions");
    expect(fullText).toContain("POST /v1/chat/interrupt");
    expect(fullText).toContain("POST /v1/app/runtime/restart");
    expect(fullText).toContain("GET /v1/app/knowledge/search");
    expect(fullText).toContain("Troubleshooting");
  });
});
