import { describe, expect, it } from "vitest";
import type { Session } from "../Session.js";
import { ResearchContractStore } from "../research/ResearchContract.js";
import { SourceLedgerStore } from "../research/SourceLedger.js";
import { buildHookContext } from "./HookContextBuilder.js";

describe("buildHookContext", () => {
  it("exposes source and research stores to hooks", () => {
    const sourceLedger = new SourceLedgerStore({ now: () => 1 });
    const researchContract = new ResearchContractStore({ now: () => 1 });
    const session = {
      meta: {
        sessionKey: "agent:main:app:test",
        channel: { channelId: "test", kind: "app" },
      },
      agent: {
        config: {
          botId: "bot-1",
          userId: "user-1",
          model: "test-model",
        },
        llm: {},
      },
      executionContract: undefined,
      sourceLedger,
      researchContract,
    } as unknown as Session;
    const sse = { agent: () => {} };

    const ctx = buildHookContext(session, sse, "turn-1", "beforeCommit");

    expect(ctx.sourceLedger).toBe(sourceLedger);
    expect(ctx.researchContract).toBe(researchContract);
  });
});
