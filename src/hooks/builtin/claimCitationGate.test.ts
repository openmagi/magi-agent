import { afterEach, describe, expect, it, vi } from "vitest";
import type { HookArgs, HookContext } from "../types.js";
import { ResearchContractStore } from "../../research/ResearchContract.js";
import { SourceLedgerStore } from "../../research/SourceLedger.js";
import { makeClaimCitationGateHook } from "./claimCitationGate.js";

function makeCtx(input: {
  sourceLedger?: SourceLedgerStore;
  researchContract?: ResearchContractStore;
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
    researchContract: input.researchContract,
  };
}

function args(
  assistantText: string,
  retryCount = 0,
  userMessage = "최신 OpenCode 도구 구성을 조사해줘.",
): HookArgs["beforeCommit"] {
  return {
    assistantText,
    toolCallCount: 2,
    toolReadHappened: true,
    userMessage,
    retryCount,
  };
}

function ledgerWithSource(): SourceLedgerStore {
  const sourceLedger = new SourceLedgerStore({ now: () => 10 });
  sourceLedger.recordSource({
    turnId: "turn-1",
    toolName: "WebFetch",
    kind: "web_fetch",
    uri: "https://github.com/anomalyco/opencode",
    title: "OpenCode",
  });
  return sourceLedger;
}

afterEach(() => {
  delete process.env.CORE_AGENT_CLAIM_CITATION_GATE;
});

describe("claim citation gate", () => {
  it("blocks source-sensitive factual claims that do not cite inspected sources", async () => {
    const researchContract = new ResearchContractStore({ now: () => 100 });
    const events: unknown[] = [];
    const hook = makeClaimCitationGateHook();

    const result = await hook.handler(
      args("OpenCode has web search and fetch-oriented research tools."),
      makeCtx({ sourceLedger: ledgerWithSource(), researchContract, events }),
    );

    expect(result).toMatchObject({ action: "block" });
    expect(researchContract.claimsForTurn("turn-1")).toMatchObject([
      {
        claimId: "claim_1",
        status: "missing",
        sourceIds: [],
      },
    ]);
    expect(events).toMatchObject([
      {
        type: "rule_check",
        ruleId: "claim-citation-gate",
        verdict: "violation",
      },
    ]);
  });

  it("allows claims cited with source ids or inspected source URLs", async () => {
    const hook = makeClaimCitationGateHook();
    const sourceLedger = ledgerWithSource();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    await expect(
      hook.handler(
        args("OpenCode has public research tooling [src_1]."),
        makeCtx({ sourceLedger, researchContract }),
      ),
    ).resolves.toEqual({ action: "continue" });

    await expect(
      hook.handler(
        args("The repository is at https://github.com/anomalyco/opencode."),
        makeCtx({ sourceLedger, researchContract }),
      ),
    ).resolves.toEqual({ action: "continue" });
  });

  it("blocks source-sensitive claims when no source was inspected", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args("OpenCode currently provides dedicated research tools."),
      makeCtx({ sourceLedger: new SourceLedgerStore(), researchContract }),
    );

    expect(result.action).toBe("block");
    expect(result.reason).toContain("WebSearch");
    expect(result.reason).toContain("WebFetch");
  });

  it("allows explicitly uncertain claims without citation", async () => {
    const hook = makeClaimCitationGateHook();

    const result = await hook.handler(
      args("OpenCode may have changed recently, so this needs manual confirmation."),
      makeCtx({
        sourceLedger: ledgerWithSource(),
        researchContract: new ResearchContractStore({ now: () => 100 }),
      }),
    );

    expect(result).toEqual({ action: "continue" });
  });

  it("fails open after the retry budget is exhausted", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args("OpenCode has web research tools.", 1),
      makeCtx({ sourceLedger: ledgerWithSource(), researchContract }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toMatchObject([
      {
        status: "missing",
      },
    ]);
  });

  it("can be disabled by environment", async () => {
    process.env.CORE_AGENT_CLAIM_CITATION_GATE = "off";
    const hook = makeClaimCitationGateHook();

    await expect(
      hook.handler(
        args("OpenCode has web research tools."),
        makeCtx({
          sourceLedger: ledgerWithSource(),
          researchContract: new ResearchContractStore(),
        }),
      ),
    ).resolves.toEqual({ action: "continue" });
  });
});
