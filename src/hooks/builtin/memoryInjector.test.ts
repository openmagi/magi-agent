/**
 * memoryInjector unit tests — T1-01.
 * Uses a mock `fetch` (passed through searchQmd) + direct handler
 * invocation so we never hit the network.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  makeMemoryInjectorHook,
  buildMemoryFence,
  searchQmd,
} from "./memoryInjector.js";
import type { HipocampusService } from "../../services/memory/HipocampusService.js";
import type { HookContext } from "../types.js";
import type { LLMClient, LLMMessage } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";

function makeLLMStub(): LLMClient {
  // No hook in this file actually calls the LLM, but HookContext
  // types require a client.
  return {} as unknown as LLMClient;
}

function makeCtx(
  executionContract?: ExecutionContractStore,
  overrides: Partial<HookContext> = {},
): {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string; data?: object }>;
} {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string; data?: object }> = [];
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: makeLLMStub(),
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg, data) => logs.push({ level, msg, data }),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    ...(executionContract ? { executionContract } : {}),
    ...overrides,
  };
  return { ctx, emitted, logs };
}

function userMessages(text: string): LLMMessage[] {
  return [{ role: "user", content: text }];
}

function makeFetchOk(results: unknown): typeof fetch {
  return (async (): Promise<Response> => {
    return new Response(JSON.stringify({ results }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

function makeFetchStatus(status: number): typeof fetch {
  return (async (): Promise<Response> => {
    return new Response("err", { status });
  }) as unknown as typeof fetch;
}

function makeFetchHang(): typeof fetch {
  return ((_url: unknown, init?: { signal?: AbortSignal }): Promise<Response> => {
    return new Promise<Response>((_resolve, reject) => {
      // Respect the abort signal if provided — mirrors real fetch.
      const signal = init?.signal;
      if (signal) {
        signal.addEventListener("abort", () => reject(new Error("aborted")), {
          once: true,
        });
      }
      // Otherwise hang forever.
    });
  }) as unknown as typeof fetch;
}

describe("memoryInjector", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    delete process.env.CORE_AGENT_MEMORY_INJECTION;
    delete process.env.QMD_URL;
    delete process.env.CORE_AGENT_MEMORY_INJECT_COLLECTION;
    delete process.env.CORE_AGENT_MEMORY_INJECT_LIMIT;
    delete process.env.CORE_AGENT_MEMORY_INJECT_MIN_SCORE;
  });

  afterEach(() => {
    // Restore env to avoid cross-test contamination.
    for (const k of [
      "CORE_AGENT_MEMORY_INJECTION",
      "QMD_URL",
      "CORE_AGENT_MEMORY_INJECT_COLLECTION",
      "CORE_AGENT_MEMORY_INJECT_LIMIT",
      "CORE_AGENT_MEMORY_INJECT_MIN_SCORE",
    ]) {
      if (originalEnv[k] === undefined) delete process.env[k];
      else process.env[k] = originalEnv[k];
    }
  });

  it("env=off → noop", async () => {
    process.env.CORE_AGENT_MEMORY_INJECTION = "off";
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(
      {
        messages: userMessages("Tell me about Helsinki cluster"),
        tools: [],
        system: "SYSTEM",
        iteration: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(emitted.length).toBe(0);
  });

  it("incognito memory mode → skips qmd and root memory injection", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted } = makeCtx(undefined, { memoryMode: "incognito" });
    const fetchSpy = vi.fn(async () => {
      throw new Error("qmd should not be called");
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    try {
      const result = await hook.handler(
        {
          messages: userMessages("Tell me about Helsinki cluster"),
          tools: [],
          system: "SYSTEM",
          iteration: 0,
        },
        ctx,
      );
      expect(result).toEqual({ action: "continue" });
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(emitted.length).toBe(0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("read-only memory mode → still injects recalled memory", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx } = makeCtx(undefined, { memoryMode: "read_only" });

    const originalFetch = globalThis.fetch;
    globalThis.fetch = makeFetchOk([
      {
        path: "memory/2026-05-08.md",
        content: "The user prefers concise Korean replies.",
        score: 0.9,
      },
    ]);

    try {
      const result = await hook.handler(
        {
          messages: userMessages("What do you remember about my style?"),
          tools: [],
          system: "SYSTEM",
          iteration: 0,
        },
        ctx,
      );
      expect(result).toBeDefined();
      expect(result!.action).toBe("replace");
      if (result && result.action === "replace") {
        expect(result.value.system).toContain('<memory-context source="qmd" tier="L0">');
        expect(result.value.system).toContain("[path: memory/2026-05-08.md]");
      }
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("qmd returns results → injected as fenced system attachment", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted } = makeCtx();

    // Patch global fetch for this test.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = makeFetchOk([
      {
        path: "memory/2026-04-19.md",
        content: "Helsinki cluster migrated on 2026-03-19.",
        score: 0.9,
      },
      {
        path: "memory/weekly-2026-W16.md",
        content: "AEF v6 landed; 33 bots hotpatched.",
        score: 0.8,
      },
    ]);

    try {
      const result = await hook.handler(
        {
          messages: userMessages("Tell me about Helsinki cluster"),
          tools: [],
          system: "SYSTEM",
          iteration: 0,
        },
        ctx,
      );
      expect(result).toBeDefined();
      expect(result!.action).toBe("replace");
      if (result && result.action === "replace") {
        expect(result.value.system).toContain('<memory-continuity-policy hidden="true">');
        expect(result.value.system).toContain('<memory-context source="qmd" tier="L0">');
        expect(result.value.system).toContain("[path: memory/2026-04-19.md]");
        expect(result.value.system).toContain("[continuity: related]");
        expect(result.value.system).toContain("[path: memory/weekly-2026-W16.md]");
        expect(result.value.system).toContain("[continuity: background]");
        expect(result.value.system).toContain("</memory-context>");
        // Original system prompt must be preserved.
        expect(result.value.system).toContain("SYSTEM");
      }
      expect(
        emitted.some(
          (e) => e.type === "rule_check" && e.ruleId === "memory-injector",
        ),
      ).toBe(true);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("qmd timeout → fails open", async () => {
    const hanging = makeFetchHang();
    const result = await searchQmd(
      "http://qmd:8080",
      { query: "x", collection: "memory", limit: 5, minScore: 0.3 },
      10, // 10ms budget forces the abort path immediately
      hanging,
    );
    expect(result).toBeNull();
  });

  it("qmd HTTP 500 → fails open", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted, logs } = makeCtx();

    const originalFetch = globalThis.fetch;
    globalThis.fetch = makeFetchStatus(500);
    try {
      const result = await hook.handler(
        {
          messages: userMessages("hi"),
          tools: [],
          system: "SYS",
          iteration: 0,
        },
        ctx,
      );
      expect(result).toEqual({ action: "continue" });
      expect(emitted.length).toBe(0);
      expect(logs.some((l) => l.msg.includes("qmd unreachable or error"))).toBe(true);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("no user message → skip", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted } = makeCtx();
    const result = await hook.handler(
      {
        messages: [],
        tools: [],
        system: "SYS",
        iteration: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(emitted.length).toBe(0);
  });

  it("not first iteration (assistant already responded) → skip", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted } = makeCtx();

    // Simulate iteration>0 branch.
    const result = await hook.handler(
      {
        messages: userMessages("hi"),
        tools: [],
        system: "SYS",
        iteration: 1,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });

    // Also verify the "last message is assistant" branch on iteration 0.
    const result2 = await hook.handler(
      {
        messages: [
          { role: "user", content: "original" },
          { role: "assistant", content: "partial answer" },
        ],
        tools: [],
        system: "SYS",
        iteration: 0,
      },
      ctx,
    );
    expect(result2).toEqual({ action: "continue" });
    expect(emitted.length).toBe(0);
  });

  it("total results > 5KB → truncated", async () => {
    // 2KB per block * 4 blocks = 8KB > 5KB cap.
    const big = "x".repeat(2_000);
    const { fence, bytes } = buildMemoryFence([
      { path: "memory/a.md", content: big, score: 1 },
      { path: "memory/b.md", content: big, score: 1 },
      { path: "memory/c.md", content: big, score: 1 },
      { path: "memory/d.md", content: big, score: 1 },
    ]);
    expect(bytes).toBeLessThanOrEqual(5_000);
    expect(fence).toContain('<memory-context source="qmd" tier="L0">');
    expect(fence).toContain("</memory-context>");
    // At least the first entry must be present.
    expect(fence).toContain("[path: memory/a.md]");
    // The last entry should not fit.
    expect(fence).not.toContain("[path: memory/d.md]");
  });

  it("audit event emitted on success", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const { ctx, emitted, logs } = makeCtx();

    const originalFetch = globalThis.fetch;
    globalThis.fetch = makeFetchOk([
      {
        path: "memory/note.md",
        content: "important note",
        score: 0.9,
      },
    ]);
    try {
      await hook.handler(
        {
          messages: userMessages("what do you remember?"),
          tools: [],
          system: "SYS",
          iteration: 0,
        },
        ctx,
      );
      const rc = emitted.find(
        (e) => e.type === "rule_check" && e.ruleId === "memory-injector",
      );
      expect(rc).toBeDefined();
      if (rc && rc.type === "rule_check") {
        expect(rc.verdict).toBe("ok");
        expect(rc.detail).toContain("injected=");
        expect(rc.detail).toContain("bytes=");
      }
      expect(logs.some((l) => l.msg === "[memoryInjector] memory_injected")).toBe(true);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("records memory recall metadata on the execution contract", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const contract = new ExecutionContractStore({ now: () => 456 });
    const { ctx } = makeCtx(contract);

    const originalFetch = globalThis.fetch;
    globalThis.fetch = makeFetchOk([
      {
        path: "memory/old.md",
        content: "SYNC 한국식 vs 일본식 이름 선택을 결정해야 한다.",
        score: 0.9,
      },
    ]);
    try {
      await hook.handler(
        {
          messages: userMessages("SYNC 분량 지금 어느 정도야?"),
          tools: [],
          system: "SYS",
          iteration: 0,
        },
        ctx,
      );

      expect(contract.memoryRecallForTurn("turn-test")).toEqual([
        expect.objectContaining({
          turnId: "turn-test",
          source: "qmd",
          path: "memory/old.md",
          continuity: "related",
          distinctivePhrases: expect.arrayContaining(["sync 한국식 vs 일본식 이름"]),
          recordedAt: 456,
        }),
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("records metadata only for qmd recall entries that are injected", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    const hook = makeMemoryInjectorHook();
    const contract = new ExecutionContractStore({ now: () => 789 });
    const { ctx } = makeCtx(contract);

    const originalFetch = globalThis.fetch;
    globalThis.fetch = makeFetchOk([
      {
        path: "memory/first.md",
        content: "primary recall ".repeat(600),
        score: 0.9,
      },
      {
        path: "memory/second.md",
        content: "secondary recall should not be tracked if it was not injected",
        score: 0.8,
      },
    ]);
    try {
      await hook.handler(
        {
          messages: userMessages("what is the state?"),
          tools: [],
          system: "SYS",
          iteration: 0,
        },
        ctx,
      );

      expect(contract.memoryRecallForTurn("turn-test").map((record) => record.path)).toEqual([
        "memory/first.md",
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("workspace agent.config.yaml memory_injection=off overrides env on", async () => {
    process.env.QMD_URL = "http://qmd:8080";
    // Create a temp workspace with agent.config.yaml opting out.
    const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "mi-"));
    try {
      await fs.writeFile(
        path.join(tmp, "agent.config.yaml"),
        "memory_injection: off\n",
        "utf8",
      );
      const hook = makeMemoryInjectorHook({ workspaceRoot: tmp });
      const { ctx, emitted } = makeCtx();

      const originalFetch = globalThis.fetch;
      // If the hook ignored the config and hit fetch it would return
      // results → emitted would be non-empty. The test proves the
      // override short-circuits before fetch.
      let fetchCalled = false;
      globalThis.fetch = ((): Promise<Response> => {
        fetchCalled = true;
        return Promise.resolve(new Response("{}", { status: 200 }));
      }) as unknown as typeof fetch;
      try {
        const result = await hook.handler(
          {
            messages: userMessages("hi"),
            tools: [],
            system: "SYS",
            iteration: 0,
          },
          ctx,
        );
        expect(result).toEqual({ action: "continue" });
        expect(fetchCalled).toBe(false);
        expect(emitted.length).toBe(0);
      } finally {
        globalThis.fetch = originalFetch;
      }
    } finally {
      await fs.rm(tmp, { recursive: true, force: true });
    }
  });

  it("injects root memory before qmd recall when hipocampus service is available", async () => {
    const hook = makeMemoryInjectorHook({
      hipocampus: {
        recall: async () => ({
          root: {
            path: "memory/ROOT.md",
            content: "stable root summary",
            bytes: Buffer.byteLength("stable root summary", "utf8"),
          },
          results: [
            {
              path: "memory/daily/2026-04-25.md",
              content: "narrow matching recall",
              score: 0.88,
            },
          ],
        }),
      } as Pick<HipocampusService, "recall"> as never,
    });
    const { ctx } = makeCtx();

    const result = await hook.handler(
      {
        messages: userMessages("what is the current state?"),
        tools: [],
        system: "SYS",
        iteration: 0,
      },
      ctx,
    );

    expect(result).toBeDefined();
    expect(result?.action).toBe("replace");
    if (result && result.action === "replace") {
      expect(result.value.system).toContain('<memory-continuity-policy hidden="true">');
      expect(result.value.system).toContain('<memory-root source="hipocampus-root" tier="L2" continuity="background">');
      expect(result.value.system).toContain("stable root summary");
      expect(result.value.system).toContain('<memory-context source="qmd" tier="L0">');
      expect(result.value.system).toContain("[continuity: background]");
      expect(result.value.system).toContain("narrow matching recall");
    }
  });

  it("skips long-term memory recall when source authority disables memory", async () => {
    const contract = new ExecutionContractStore({ now: () => 111 });
    contract.replaceSourceAuthorityForTurn("turn-test", [
      {
        turnId: "turn-test",
        currentSourceKinds: ["attachment"],
        longTermMemoryPolicy: "disabled",
        classifierReason: "Latest user message says not to use prior memory.",
      },
    ]);
    const hook = makeMemoryInjectorHook({
      hipocampus: {
        recall: async () => {
          throw new Error("memory should not be called");
        },
      } as Pick<HipocampusService, "recall"> as never,
    });
    const { ctx, emitted } = makeCtx(contract);

    const result = await hook.handler(
      {
        messages: userMessages("과거 메모리 참조하지 말고 이 파일 기준으로 답해"),
        tools: [],
        system: "SYS",
        iteration: 0,
      },
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(contract.memoryRecallForTurn("turn-test")).toEqual([]);
    expect(emitted).toContainEqual(expect.objectContaining({
      type: "rule_check",
      ruleId: "memory-injector",
      verdict: "ok",
    }));
  });

  it("forces recalled memory to background when source authority is background-only", async () => {
    const contract = new ExecutionContractStore({ now: () => 222 });
    contract.replaceSourceAuthorityForTurn("turn-test", [
      {
        turnId: "turn-test",
        currentSourceKinds: ["selected_kb"],
        longTermMemoryPolicy: "background_only",
        classifierReason: "Selected KB is the current source of truth.",
      },
    ]);
    const hook = makeMemoryInjectorHook({
      hipocampus: {
        recall: async () => ({
          root: null,
          results: [
            {
              path: "memory/daily/old.md",
              content: "이 파일 기준으로 답했던 과거 메모리",
              score: 0.95,
            },
          ],
        }),
      } as Pick<HipocampusService, "recall"> as never,
    });
    const { ctx } = makeCtx(contract);

    const result = await hook.handler(
      {
        messages: userMessages("이 파일 기준으로 답해"),
        tools: [],
        system: "SYS",
        iteration: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("[continuity: background]");
      expect(result.value.system).toContain('authority="L4"');
    }
    expect(contract.memoryRecallForTurn("turn-test")[0]).toMatchObject({
      continuity: "background",
    });
  });
});
