import { afterEach, describe, expect, it, vi } from "vitest";
import type { HookArgs, HookContext } from "../types.js";
import { ResearchContractStore } from "../../research/ResearchContract.js";
import { SourceLedgerStore } from "../../research/SourceLedger.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { makeClaimCitationGateHook } from "./claimCitationGate.js";

function requestClassifierLlm(sourceSensitive: boolean): HookContext["llm"] {
  return {
    stream: () =>
      (async function* () {
        yield {
          kind: "text_delta" as const,
          delta: JSON.stringify({
            turnMode: { label: "other", confidence: 0.8 },
            skipTdd: false,
            implementationIntent: false,
            documentOrFileOperation: false,
            deterministic: {
              requiresDeterministic: false,
              kinds: [],
              reason: "No exact calculation.",
              suggestedTools: [],
              acceptanceCriteria: [],
            },
            fileDelivery: {
              intent: "none",
              path: null,
              wantsChatDelivery: false,
              wantsKbDelivery: false,
              wantsFileOutput: false,
            },
            goalProgress: {
              requiresAction: !sourceSensitive,
              actionKinds: sourceSensitive ? [] : ["browser_interaction"],
              reason: sourceSensitive
                ? "The request is research-like."
                : "The request requires runtime interaction rather than source-grounded research.",
            },
            research: {
              sourceSensitive,
              reason: sourceSensitive
                ? "The user asks for source-grounded factual research."
                : "The user asks for UI operation, not a sourced factual answer.",
            },
          }),
        };
        yield { kind: "message_end" as const };
      })(),
  } as HookContext["llm"];
}

function makeCtx(input: {
  sourceLedger?: SourceLedgerStore;
  researchContract?: ResearchContractStore;
  llm?: HookContext["llm"];
  events?: unknown[];
  transcript?: TranscriptEntry[];
} = {}): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "agent:main:app:test",
    turnId: "turn-1",
    llm: input.llm ?? requestClassifierLlm(true),
    agentModel: "test-model",
    transcript: input.transcript ?? [],
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
    toolNames: [],
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

function ledgerWithLocalFileSource(): SourceLedgerStore {
  const sourceLedger = new SourceLedgerStore({ now: () => 10 });
  sourceLedger.recordSource({
    turnId: "turn-1",
    toolName: "FileRead",
    kind: "file",
    uri: "file:///workspace/.magi/coding-benchmark-reports/latest.json",
    title: "CodingBenchmark latest report",
    snippets: ["Coding benchmark summary: 1 passed run out of 1 recorded run."],
  });
  return sourceLedger;
}

function ledgerWithBrowserSource(): SourceLedgerStore {
  const sourceLedger = new SourceLedgerStore({ now: () => 10 });
  sourceLedger.recordSource({
    turnId: "turn-1",
    toolName: "Browser",
    kind: "browser",
    uri: "https://example.com",
    title: "Example Domain",
    snippets: [
      "Example Domain This domain is for use in illustrative examples in documents.",
    ],
    metadata: { action: "snapshot" },
  });
  return sourceLedger;
}

function successfulCodingBenchmarkTranscript(): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 1,
      turnId: "turn-1",
      toolUseId: "bench-1",
      name: "CodingBenchmark",
      input: { action: "report" },
    },
    {
      kind: "tool_result",
      ts: 2,
      turnId: "turn-1",
      toolUseId: "bench-1",
      status: "ok",
      output: JSON.stringify({
        report: {
          summary: { totalRuns: 1, passedRuns: 1, successRate: 1 },
        },
      }),
      metadata: { evidenceKind: "benchmark_report" },
    },
  ];
}

afterEach(() => {
  delete process.env.MAGI_CLAIM_CITATION_GATE;
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
    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "rule_check",
          ruleId: "claim-citation-gate",
          verdict: "violation",
        }),
        expect.objectContaining({
          type: "research_artifact_delta",
        }),
      ]),
    );
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

  it("enforces classifier source sensitivity when the research contract store is unavailable", async () => {
    const hook = makeClaimCitationGateHook();

    const result = await hook.handler(
      args("OpenCode currently provides dedicated research tools."),
      makeCtx({
        sourceLedger: new SourceLedgerStore(),
        llm: requestClassifierLlm(true),
      }),
    );

    expect(result.action).toBe("block");
    expect(result.reason).toContain("WebSearch");
  });

  it("uses the request classifier instead of keyword matching for UI test instructions", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        "The web service UI is ready for human-like browser testing.",
        0,
        "현재 웹서비스를 API 입력 방식으로 우회하지 말고 실제 사람이 쓰는 것처럼 브라우저에서 테스트해줘.",
      ),
      makeCtx({
        sourceLedger: new SourceLedgerStore(),
        researchContract,
        llm: requestClassifierLlm(false),
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.turnFor("turn-1")).toMatchObject({
      sourceSensitive: false,
      requiredSourceKinds: [],
    });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("does not require fresh sources for short follow-up option selection", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        "A is the better path because the venture frame has a clearer IC memo structure.",
        0,
        "위 리서치 기준으로 어느 쪽?",
      ),
      makeCtx({ sourceLedger: new SourceLedgerStore(), researchContract }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("still requires fresh sources when a follow-up explicitly asks to reverify current facts", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        "A is the better path because the current source review has stronger evidence.",
        0,
        "최근 자료 다시 확인해서 어느 쪽인지 골라줘.",
      ),
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

  it("allows the final answer after the retry budget is exhausted and leaves a citation warning", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });
    const events: unknown[] = [];
    const ctx = makeCtx({ sourceLedger: ledgerWithSource(), researchContract, events });

    const result = await hook.handler(
      args("OpenCode has web research tools.", 1),
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toMatchObject([
      {
        status: "missing",
      },
    ]);
    expect(events).toContainEqual({
      type: "rule_check",
      ruleId: "claim-citation-gate",
      verdict: "violation",
      detail: "1 uncited claims",
    });
    expect(events).toContainEqual({
      type: "research_artifact_delta",
      claims: [
        {
          claimId: "claim_1",
          text: "OpenCode has web research tools.",
          claimType: "fact",
          supportStatus: "unsupported",
          sourceIds: [],
          confidence: 0.25,
          reasoning: {
            premiseSourceIds: [],
            inference: "",
            assumptions: ["No inspected source identifiers covered this claim."],
            status: "missing_source_support",
          },
        },
      ],
      claimSourceLinks: [],
    });
    expect(ctx.log).toHaveBeenCalledWith(
      "warn",
      "[claim-citation-gate] retry exhausted; failing open with citation warning",
      expect.objectContaining({ missing: 1 }),
    );
  });

  it("does not require research citations for successful local CodingBenchmark evidence", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        "The js-bugfix-arithmetic coding benchmark has a 1/1 pass report.",
        1,
        "Run the coding benchmark smoke and tell me the result.",
      ),
      makeCtx({
        sourceLedger: ledgerWithLocalFileSource(),
        researchContract,
        transcript: successfulCodingBenchmarkTranscript(),
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("does not require research citations for CodingBenchmark evidence even when other source records exist", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        "The js-harness-worktree-adoption coding benchmark has a 1/1 pass report.",
        1,
        "Run exactly one live coding benchmark task with CodingBenchmark.",
      ),
      makeCtx({
        sourceLedger: ledgerWithSources(),
        researchContract,
        transcript: successfulCodingBenchmarkTranscript(),
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("does not require research citations for ordinary browser tool smoke results", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        [
          "브라우저 기본 기능 테스트 완료했습니다.",
          "The Browser tool has working session, open, snapshot, and close support.",
          "The CDP endpoint is available and snapshot refs are usable.",
        ].join("\n"),
        1,
        "브라우저 세션 띄우고 간단한 페이지 하나 열어서 기본 기능 테스트해줘.",
      ),
      makeCtx({
        sourceLedger: ledgerWithBrowserSource(),
        researchContract,
        llm: requestClassifierLlm(false),
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("still enforces citations for explicit research turns that used browser sources", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      args(
        "The page currently has a heading and two paragraphs.",
        0,
        "현재 example.com 페이지 내용을 조사해서 근거와 함께 알려줘.",
      ),
      makeCtx({
        sourceLedger: ledgerWithBrowserSource(),
        researchContract,
      }),
    );

    expect(result.action).toBe("block");
    expect(result.reason).toContain("[RETRY:CLAIM_CITATION]");
  });

  it("uses live beforeCommit tool names for local CodingBenchmark evidence when transcript is unavailable", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      {
        ...args(
          "The js-bugfix-arithmetic benchmark has a 1/1 pass report and contains verified file changes.",
          1,
          "Run exactly one live coding benchmark task with CodingBenchmark.",
        ),
        toolNames: ["CodingBenchmark", "FileRead", "FileEdit", "TestRun", "GitDiff"],
      },
      makeCtx({
        sourceLedger: ledgerWithLocalFileSource(),
        researchContract,
        transcript: [],
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("uses live local coding evidence even when no source ledger records were captured", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      {
        ...args(
          "The security benchmark has a 1/1 pass report and tests are passing.",
          1,
          "Fix the security bug in the local repository and verify it with tests.",
        ),
        toolNames: ["CodingBenchmark", "FileWrite", "SafeCommand", "TestRun", "GitDiff"],
      },
      makeCtx({
        sourceLedger: new SourceLedgerStore(),
        researchContract,
        transcript: [],
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
  });

  it("uses live test and diff evidence for local coding turns with an empty source ledger", async () => {
    const hook = makeClaimCitationGateHook();
    const researchContract = new ResearchContractStore({ now: () => 100 });

    const result = await hook.handler(
      {
        ...args(
          "The local bug fix has passing tests and includes the expected GitDiff.",
          1,
          "Fix the code bug in this repository and verify it with tests.",
        ),
        toolNames: ["FileWrite", "TestRun", "GitDiff"],
      },
      makeCtx({
        sourceLedger: new SourceLedgerStore(),
        researchContract,
        transcript: [],
      }),
    );

    expect(result).toEqual({ action: "continue" });
    expect(researchContract.claimsForTurn("turn-1")).toEqual([]);
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
    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "rule_check",
          ruleId: "claim-citation-gate",
          verdict: "violation",
        }),
        expect.objectContaining({
          type: "research_artifact_delta",
        }),
      ]),
    );
    expect(ctx.log).toHaveBeenCalledWith(
      "warn",
      "[claim-citation-gate] long sourced draft has partial citation gaps; failing open",
      expect.objectContaining({ missing: 1, sources: 3 }),
    );
  });

  it("can be disabled by environment", async () => {
    process.env.MAGI_CLAIM_CITATION_GATE = "off";
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
