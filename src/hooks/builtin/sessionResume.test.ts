/**
 * Tests for sessionResumeHook (Layer 4 meta-cognitive scaffolding).
 */

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  _clearSessionResumeMemo,
  buildSessionResumeBlock,
  classifyResumeTurnIntent,
  extractAbandonedTurn,
  extractRecentTurns,
  makeSessionResumeHook,
  type SessionResumeAgent,
  type SessionResumeSnapshot,
} from "./sessionResume.js";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

function makeCtx(sessionKey = "sess-1", overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey,
    turnId: "turn-new",
    llm: {} as never,
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    ...overrides,
  };
}

function makeCtxWithClassifierReply(
  reply: string,
  sessionKey = "sess-1",
): HookContext {
  return makeCtx(sessionKey, {
    llm: {
      async *stream() {
        yield { kind: "text_delta", delta: reply, blockIndex: 0 };
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        };
      },
    } as never,
  });
}

function makeAgent(
  snapshotProvider: (sessionKey: string) => SessionResumeSnapshot | null,
): { agent: SessionResumeAgent; appended: { sessionKey: string; seed: string }[] } {
  const appended: { sessionKey: string; seed: string }[] = [];
  const agent: SessionResumeAgent = {
    getResumeSnapshot: async (sessionKey) => snapshotProvider(sessionKey),
    appendResumeSeed: async (sessionKey, seed) => {
      appended.push({ sessionKey, seed });
    },
  };
  return { agent, appended };
}

async function mkTmpWorkspace(): Promise<string> {
  return await fs.mkdtemp(path.join(os.tmpdir(), "session-resume-"));
}

afterEach(() => {
  delete process.env.CORE_AGENT_SESSION_RESUME_SEED;
  _clearSessionResumeMemo();
});

beforeEach(() => {
  _clearSessionResumeMemo();
});

describe("extractRecentTurns", () => {
  it("only returns committed turns with user+assistant pairs", () => {
    const transcript: TranscriptEntry[] = [
      { kind: "user_message", ts: 1, turnId: "t1", text: "hi" },
      { kind: "assistant_text", ts: 2, turnId: "t1", text: "hello" },
      { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },

      { kind: "user_message", ts: 4, turnId: "t2", text: "uncommitted q" },
      { kind: "assistant_text", ts: 5, turnId: "t2", text: "uncommitted a" },
      // no turn_committed for t2

      { kind: "user_message", ts: 6, turnId: "t3", text: "second committed" },
      { kind: "assistant_text", ts: 7, turnId: "t3", text: "second a" },
      { kind: "turn_committed", ts: 8, turnId: "t3", inputTokens: 1, outputTokens: 1 },
    ];
    const turns = extractRecentTurns(transcript, 3);
    expect(turns.map((t) => t.turnId)).toEqual(["t1", "t3"]);
    expect(turns[0]?.user).toBe("hi");
    expect(turns[0]?.assistant).toBe("hello");
  });

  it("returns at most `maxPairs` most-recent turns", () => {
    const entries: TranscriptEntry[] = [];
    for (let i = 0; i < 5; i++) {
      entries.push(
        { kind: "user_message", ts: i * 3, turnId: `t${i}`, text: `q${i}` },
        { kind: "assistant_text", ts: i * 3 + 1, turnId: `t${i}`, text: `a${i}` },
        { kind: "turn_committed", ts: i * 3 + 2, turnId: `t${i}`, inputTokens: 1, outputTokens: 1 },
      );
    }
    const turns = extractRecentTurns(entries, 3);
    expect(turns.map((t) => t.turnId)).toEqual(["t2", "t3", "t4"]);
  });
});

describe("extractAbandonedTurn", () => {
  it("summarizes the latest uncommitted turn after a pod restart", () => {
    const transcript: TranscriptEntry[] = [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "make the IC report" },
      {
        kind: "tool_call",
        ts: 3,
        turnId: "t1",
        toolUseId: "tool-1",
        name: "SpawnAgent",
        input: { persona: "ic-chair", prompt: "write synthesis.md" },
      },
    ];

    const abandoned = extractAbandonedTurn(transcript);

    expect(abandoned).toMatchObject({
      turnId: "t1",
      user: "make the IC report",
      toolCalls: ["SpawnAgent"],
      committed: false,
    });
  });

  it("ignores committed or aborted turns", () => {
    const transcript: TranscriptEntry[] = [
      { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
      { kind: "user_message", ts: 2, turnId: "t1", text: "done" },
      { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
      { kind: "turn_started", ts: 4, turnId: "t2", declaredRoute: "direct" },
      { kind: "user_message", ts: 5, turnId: "t2", text: "aborted" },
      { kind: "turn_aborted", ts: 6, turnId: "t2", reason: "user_interrupt" },
    ];

    expect(extractAbandonedTurn(transcript)).toBeNull();
  });
});

describe("buildSessionResumeBlock", () => {
  it("truncates long messages to 800 chars with ellipsis", async () => {
    const long = "x".repeat(2000);
    const snapshot: SessionResumeSnapshot = {
      transcript: [
        { kind: "user_message", ts: 1, turnId: "t1", text: long },
        { kind: "assistant_text", ts: 2, turnId: "t1", text: long },
        { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
      ],
      lastActivityAt: Date.now(),
    };
    const root = await mkTmpWorkspace();
    try {
      const block = await buildSessionResumeBlock(snapshot, root);
      expect(block).toContain("<session_resume>");
      expect(block).toContain("</session_resume>");
      expect(block).toContain("…");
      // Bound total user line ~ 800 + "User: " overhead.
      const userLine = block.split("\n").find((l) => l.startsWith("User:"));
      expect(userLine).toBeTruthy();
      expect(userLine!.length).toBeLessThanOrEqual(900);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("returns empty string when no turns and no recent files", async () => {
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [],
        lastActivityAt: Date.now(),
      };
      const block = await buildSessionResumeBlock(snapshot, root);
      expect(block).toBe("");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("includes an abandoned prior turn even when no turn committed", async () => {
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
          { kind: "user_message", ts: 2, turnId: "t1", text: "make the IC report" },
          {
            kind: "tool_call",
            ts: 3,
            turnId: "t1",
            toolUseId: "tool-1",
            name: "SpawnAgent",
            input: { persona: "ic-chair", prompt: "write synthesis.md" },
          },
        ],
        lastActivityAt: Date.now(),
      };

      const block = await buildSessionResumeBlock(snapshot, root);

      expect(block).toContain("## Interrupted prior turn");
      expect(block).toContain("make the IC report");
      expect(block).toContain("SpawnAgent");
      expect(block).toContain("did not reach turn_committed or turn_aborted");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("adds a critical active-work resume packet when classifier says the user asks about current work", async () => {
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
          { kind: "user_message", ts: 2, turnId: "t1", text: "make the IC report" },
          {
            kind: "tool_call",
            ts: 3,
            turnId: "t1",
            toolUseId: "tool-1",
            name: "SpawnAgent",
            input: { persona: "ic-chair", prompt: "write synthesis.md" },
          },
        ],
        lastActivityAt: Date.now(),
      };

      const block = await buildSessionResumeBlock(snapshot, root, {
        turnIntent: "resume_or_status_current_work",
      });

      expect(block).toContain("<active_work_resume");
      expect(block).toContain('priority="critical"');
      expect(block).toContain("classifier_intent: resume_or_status_current_work");
      expect(block).toContain("make the IC report");
      expect(block).toContain("Do not answer generically");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("includes recently-modified files within 24h of last activity", async () => {
    const root = await mkTmpWorkspace();
    try {
      const now = Date.now();
      await fs.writeFile(path.join(root, "fresh.md"), "x");
      await fs.utimes(path.join(root, "fresh.md"), new Date(now - 1000), new Date(now - 1000));
      await fs.writeFile(path.join(root, "stale.md"), "y");
      const longAgo = new Date(now - 7 * 24 * 3600 * 1000);
      await fs.utimes(path.join(root, "stale.md"), longAgo, longAgo);

      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "user_message", ts: 1, turnId: "t1", text: "hi" },
          { kind: "assistant_text", ts: 2, turnId: "t1", text: "hello" },
          { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
        ],
        lastActivityAt: now,
      };
      const block = await buildSessionResumeBlock(snapshot, root);
      expect(block).toContain("fresh.md");
      expect(block).not.toContain("stale.md");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});

describe("classifyResumeTurnIntent", () => {
  it("uses the LLM classifier enum for resumed-work continuation intent", async () => {
    const ctx = makeCtxWithClassifierReply("resume_or_status_current_work");
    await expect(
      classifyResumeTurnIntent("왜 멈췄지?", ctx, {
        transcript: [
          { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
          { kind: "user_message", ts: 2, turnId: "t1", text: "make the IC report" },
        ],
        lastActivityAt: Date.now(),
      }),
    ).resolves.toBe("resume_or_status_current_work");
  });

  it("falls back to other when the classifier cannot provide a known enum", async () => {
    const ctx = makeCtxWithClassifierReply("maybe");
    await expect(
      classifyResumeTurnIntent("status?", ctx, {
        transcript: [],
        lastActivityAt: Date.now(),
      }),
    ).resolves.toBe("other");
  });
});

describe("sessionResume incognito", () => {
  it("does not read or append resume seed in incognito memory mode", async () => {
    const root = await mkTmpWorkspace();
    try {
      const agent = {
        getResumeSnapshot: vi.fn(async () => ({
          transcript: [
            { kind: "user_message", ts: 1, turnId: "t1", text: "hi" },
            { kind: "assistant_text", ts: 2, turnId: "t1", text: "hello" },
            { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
          ],
          lastActivityAt: Date.now(),
        })),
        appendResumeSeed: vi.fn(async () => undefined),
      } satisfies SessionResumeAgent;
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });

      await hook.handler(
        { userMessage: "continue" },
        makeCtx("sess-incognito", { memoryMode: "incognito" }),
      );

      expect(agent.getResumeSnapshot).not.toHaveBeenCalled();
      expect(agent.appendResumeSeed).not.toHaveBeenCalled();
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});

describe("sessionResumeHook", () => {
  it("declares name, point, priority 2, blocking, and a longer timeout than default hook deadline", () => {
    const { agent } = makeAgent(() => null);
    const hook = makeSessionResumeHook({ agent, workspaceRoot: "/tmp" });
    expect(hook.name).toBe("builtin:session-resume");
    expect(hook.point).toBe("beforeTurnStart");
    expect(hook.priority).toBe(2);
    expect(hook.blocking).toBe(true);
    expect(hook.timeoutMs).toBeGreaterThan(5_000);
  });

  it("no-ops on fresh session (snapshot returns null)", async () => {
    const root = await mkTmpWorkspace();
    try {
      const { agent, appended } = makeAgent(() => null);
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });
      const result = await hook.handler({ userMessage: "hi" }, makeCtx());
      expect(result).toEqual({ action: "continue" });
      expect(appended).toHaveLength(0);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("seeds on first turn post-resume, memoises after", async () => {
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "user_message", ts: 1, turnId: "t1", text: "prior question" },
          { kind: "assistant_text", ts: 2, turnId: "t1", text: "prior answer" },
          { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
        ],
        lastActivityAt: Date.now(),
      };
      let calls = 0;
      const { agent, appended } = makeAgent(() => {
        calls++;
        return snapshot;
      });
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });

      const r1 = await hook.handler(
        { userMessage: "new q" },
        makeCtxWithClassifierReply("other", "sess-1"),
      );
      expect(r1).toEqual({ action: "continue" });
      expect(appended).toHaveLength(1);
      expect(appended[0]?.seed).toContain("<session_resume>");
      expect(appended[0]?.seed).toContain("prior question");

      // Second call on same session — no re-seed.
      const r2 = await hook.handler({ userMessage: "another" }, makeCtx("sess-1"));
      expect(r2).toEqual({ action: "continue" });
      expect(appended).toHaveLength(1);
      // Agent should NOT have been re-queried after memoisation.
      expect(calls).toBe(1);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("uses classifier result to add active-work guidance to the seeded block", async () => {
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "turn_started", ts: 1, turnId: "t1", declaredRoute: "direct" },
          { kind: "user_message", ts: 2, turnId: "t1", text: "finish the investment memo" },
        ],
        lastActivityAt: Date.now(),
      };
      const { agent, appended } = makeAgent(() => snapshot);
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });

      const result = await hook.handler(
        { userMessage: "why did it stop?" },
        makeCtxWithClassifierReply("resume_or_status_current_work", "sess-1"),
      );

      expect(result).toEqual({ action: "continue" });
      expect(appended[0]?.seed).toContain("<active_work_resume");
      expect(appended[0]?.seed).toContain("finish the investment memo");
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("no-ops when transcript is empty", async () => {
    const root = await mkTmpWorkspace();
    try {
      const { agent, appended } = makeAgent(() => ({
        transcript: [],
        lastActivityAt: Date.now(),
      }));
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });
      const result = await hook.handler({ userMessage: "hi" }, makeCtx());
      expect(result).toEqual({ action: "continue" });
      expect(appended).toHaveLength(0);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("respects CORE_AGENT_SESSION_RESUME_SEED=off", async () => {
    process.env.CORE_AGENT_SESSION_RESUME_SEED = "off";
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "user_message", ts: 1, turnId: "t1", text: "hi" },
          { kind: "assistant_text", ts: 2, turnId: "t1", text: "hello" },
          { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
        ],
        lastActivityAt: Date.now(),
      };
      const { agent, appended } = makeAgent(() => snapshot);
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });
      const result = await hook.handler({ userMessage: "hi" }, makeCtx());
      expect(result).toEqual({ action: "continue" });
      expect(appended).toHaveLength(0);
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });

  it("fail-open when appendResumeSeed throws", async () => {
    const root = await mkTmpWorkspace();
    try {
      const snapshot: SessionResumeSnapshot = {
        transcript: [
          { kind: "user_message", ts: 1, turnId: "t1", text: "hi" },
          { kind: "assistant_text", ts: 2, turnId: "t1", text: "hello" },
          { kind: "turn_committed", ts: 3, turnId: "t1", inputTokens: 1, outputTokens: 1 },
        ],
        lastActivityAt: Date.now(),
      };
      const agent: SessionResumeAgent = {
        getResumeSnapshot: async () => snapshot,
        appendResumeSeed: async () => {
          throw new Error("persist boom");
        },
      };
      const hook = makeSessionResumeHook({ agent, workspaceRoot: root });
      const ctx = makeCtx("sess-err");
      const result = await hook.handler({ userMessage: "hi" }, ctx);
      expect(result).toEqual({ action: "continue" });
      expect(ctx.log).toHaveBeenCalledWith(
        "warn",
        expect.stringContaining("seed failed"),
        expect.any(Object),
      );
    } finally {
      await fs.rm(root, { recursive: true, force: true });
    }
  });
});
