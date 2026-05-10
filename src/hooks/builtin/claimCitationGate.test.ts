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

function ledgerWithSources(count = 1): SourceLedgerStore {
  const sourceLedger = new SourceLedgerStore({ now: () => 10 });
  for (let index = 0; index < count; index += 1) {
    sourceLedger.recordSource({
      turnId: "turn-1",
      toolName: "WebFetch",
      kind: "web_fetch",
      uri: `https://github.com/anomalyco/opencode/${index + 1}`,
      title: `OpenCode ${index + 1}`,
      snippets: [
        "OpenCode provides websearch and webfetch tools for inspecting public sources.",
      ],
    });
  }
  return sourceLedger;
}

function ledgerWithSource(): SourceLedgerStore {
  return ledgerWithSources(1);
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

  it("includes inspected source context and missing claims in retry instructions", async () => {
    const hook = makeClaimCitationGateHook();

    const result = await hook.handler(
      args("OpenCode has web search and fetch-oriented research tools."),
      makeCtx({
        sourceLedger: ledgerWithSource(),
        researchContract: new ResearchContractStore({ now: () => 100 }),
      }),
    );

    expect(result.action).toBe("block");
    expect(result.reason).toContain("Available inspected sources");
    expect(result.reason).toContain("[src_1]");
    expect(result.reason).toContain("OpenCode");
    expect(result.reason).toContain("https://github.com/anomalyco/opencode");
    expect(result.reason).toContain("websearch and webfetch");
    expect(result.reason).toContain("Missing citation examples");
    expect(result.reason).toContain("OpenCode has web search");
    expect(result.reason).toContain(
      "If a claim is not supported by these sources, remove it or mark it uncertain",
    );
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

  it("fails closed after the retry budget is exhausted", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args("OpenCode has web research tools.", 1),
      makeCtx({ sourceLedger: ledgerWithSource(), researchContract }),
    );

    expect(result).toMatchObject({ action: "block" });
    expect(result.reason).toContain("[RULE:CLAIM_CITATION_REQUIRED]");
    expect(researchContract.claimsForTurn("turn-1")).toMatchObject([
      {
        status: "missing",
      },
    ]);
  });

  it("fails closed when the verifier itself errors", async () => {
    const hook = makeClaimCitationGateHook();
    const explodingLedger = {
      sourcesForTurn: () => {
        throw new Error("source ledger unavailable");
      },
    } as unknown as SourceLedgerStore;

    const result = await hook.handler(
      args("OpenCode has web research tools."),
      makeCtx({
        sourceLedger: explodingLedger,
        researchContract: new ResearchContractStore({ now: () => 100 }),
      }),
    );

    expect(result).toMatchObject({ action: "block" });
    expect(result.reason).toContain("[RULE:CLAIM_CITATION_GATE_ERROR]");
    expect(result.reason).toContain("source ledger unavailable");
  });

  it("does not resample long source-backed drafts for a small citation gap", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });
    const events: unknown[] = [];
    const ctx = makeCtx({
      sourceLedger: ledgerWithSources(3),
      researchContract,
      events,
    });
    const citedBody = Array.from({ length: 80 }, () =>
      "OpenCode has public repository evidence for tool behavior [src_1].",
    ).join(" ");
    const draft = `${citedBody} OpenCode provides web research tools without this sentence citing a source.`;

    const result = await hook.handler(args(draft), ctx);

    expect(result).toEqual({ action: "continue" });
    expect(
      researchContract
        .claimsForTurn("turn-1")
        .some((claim) => claim.status === "missing"),
    ).toBe(true);
    expect(events).toMatchObject([
      {
        type: "rule_check",
        ruleId: "claim-citation-gate",
        verdict: "violation",
      },
    ]);
    expect(ctx.log).toHaveBeenCalledWith(
      "warn",
      "[claim-citation-gate] long sourced draft has partial citation gaps; failing open",
      expect.objectContaining({ missing: 1, sources: 3 }),
    );
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
