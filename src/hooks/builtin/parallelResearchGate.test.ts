import { describe, expect, it, vi } from "vitest";
import type { HookArgs, HookContext } from "../types.js";
import { SourceLedgerStore } from "../../research/SourceLedger.js";
import { makeParallelResearchGateHook } from "./parallelResearchGate.js";

function makeCtx(input: {
  sourceLedger?: SourceLedgerStore;
  events?: unknown[];
} = {}): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "agent:main:app:test",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    agentModel: "test-model",
    transcript: [],
    emit: (event) => input.events?.push(event),
    log: vi.fn(),
    abortSignal: AbortSignal.timeout(5_000),
    deadlineMs: 5_000,
    sourceLedger: input.sourceLedger,
  };
}

function args(
  retryCount = 0,
  userMessage = "Break this broad research question into independent subquestions, inspect sources in parallel, then synthesize.",
): HookArgs["beforeCommit"] {
  return {
    assistantText: "Here is a synthesized answer.",
    toolCallCount: 2,
    toolReadHappened: true,
    userMessage,
    retryCount,
  };
}

function ledgerWithSubagentResult(): SourceLedgerStore {
  const ledger = new SourceLedgerStore({ now: () => 10 });
  ledger.recordSource({
    turnId: "turn-1::spawn::task-a",
    toolName: "SpawnAgent",
    kind: "subagent_result",
    uri: "spawn://task-a",
    title: "research subagent result",
    snippets: ["child finding"],
  });
  return ledger;
}

describe("parallel research gate", () => {
  it("blocks broad parallel research completion without child-agent evidence", async () => {
    const hook = makeParallelResearchGateHook();
    const events: unknown[] = [];

    const result = await hook.handler(
      args(),
      makeCtx({ sourceLedger: new SourceLedgerStore(), events }),
    );

    expect(result).toMatchObject({ action: "block" });
    expect(result.reason).toContain("[RETRY:PARALLEL_RESEARCH]");
    expect(result.reason).toContain("SpawnAgent");
    expect(result.reason).toContain("research");
    expect(events).toMatchObject([
      {
        type: "rule_check",
        ruleId: "parallel-research-gate",
        verdict: "violation",
      },
    ]);
  });

  it("allows broad research when a subagent result is recorded", async () => {
    const hook = makeParallelResearchGateHook();
    const events: unknown[] = [];

    const result = await hook.handler(
      args(),
      makeCtx({ sourceLedger: ledgerWithSubagentResult(), events }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(events).toMatchObject([
      {
        type: "rule_check",
        ruleId: "parallel-research-gate",
        verdict: "ok",
      },
    ]);
  });

  it("fails open after one retry to avoid trapping the turn", async () => {
    const hook = makeParallelResearchGateHook();
    const ctx = makeCtx({ sourceLedger: new SourceLedgerStore() });

    const result = await hook.handler(args(1), ctx);

    expect(result).toEqual({ action: "continue" });
    expect(ctx.log).toHaveBeenCalledWith(
      "warn",
      "[parallel-research-gate] retry exhausted; failing open",
      expect.objectContaining({ turnId: "turn-1" }),
    );
  });

  it("ignores ordinary source-sensitive research that is not broad or parallel", async () => {
    const hook = makeParallelResearchGateHook();

    const result = await hook.handler(
      args(0, "Find the latest release notes for this API."),
      makeCtx({ sourceLedger: new SourceLedgerStore() }),
    );

    expect(result).toEqual({ action: "continue" });
  });
});
