/**
 * CommitPipeline unit tests (R3 refactor).
 *
 * Covers:
 *   • commit() — assistant_text + turn_committed transcript append,
 *     afterCommit + afterTurnEnd + onTaskCheckpoint fires
 *   • beforeCommit block → throws, transcript has NO commit markers
 *   • abort() — turn_aborted transcript, onAbort + afterTurnEnd fire,
 *     pending asks rejected
 *   • collectFilesChanged — scans FileWrite + FileEdit tool_use inputs
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServerResponse } from "node:http";
import {
  commit,
  abort,
  collectFilesChanged,
  type CommitPipelineContext,
} from "./CommitPipeline.js";
import type { LLMContentBlock } from "../transport/LLMClient.js";
import type { Session } from "../Session.js";
import { Transcript } from "../storage/Transcript.js";
import { SseWriter } from "../transport/SseWriter.js";
import type { HookContext } from "../hooks/types.js";
import type { UserMessage } from "../util/types.js";

class FakeSse extends SseWriter {
  readonly events: Array<Record<string, unknown>> = [];
  finished = 0;
  constructor() {
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }
  override agent(event: unknown): void {
    this.events.push(event as Record<string, unknown>);
  }
  override legacyDelta(): void {}
  override legacyFinish(): void {
    this.finished += 1;
  }
  override start(): void {}
  override end(): void {}
}

async function makeCtx(opts: {
  blocks: LLMContentBlock[];
  blockBeforeCommit?: string;
}): Promise<{
  ctx: CommitPipelineContext;
  sse: FakeSse;
  transcript: Transcript;
  hooks: Array<{ point: string; args: unknown }>;
  phases: string[];
  assistantTextRef: { value: string };
  rejectedReasons: string[];
}> {
  const workspaceRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "commit-pipeline-"),
  );
  const sessionsDir = path.join(workspaceRoot, "sessions");
  await fs.mkdir(sessionsDir, { recursive: true });

  const sse = new FakeSse();
  const transcript = new Transcript(sessionsDir, "sess-key");
  const hooks: Array<{ point: string; args: unknown }> = [];
  const phases: string[] = [];
  const assistantTextRef = { value: "" };
  const rejectedReasons: string[] = [];

  const agentStub = {
    hooks: {
      runPre: async (point: string, args: unknown) => {
        hooks.push({ point, args });
        if (point === "beforeCommit" && opts.blockBeforeCommit) {
          return { action: "block" as const, reason: opts.blockBeforeCommit };
        }
        return { action: "continue" as const, args };
      },
      runPost: async (point: string, args: unknown) => {
        hooks.push({ point, args });
      },
    },
  };

  const session = {
    meta: { sessionKey: "sess-key" },
    transcript,
    agent: agentStub,
  } as unknown as Session;

  const userMessage: UserMessage = {
    text: "do something",
    receivedAt: Date.now(),
  };

  const ctx: CommitPipelineContext = {
    session,
    sse,
    userMessage,
    turnId: "turn-1",
    startedAt: Date.now() - 100,
    buildHookContext: () =>
      ({
        botId: "b",
        userId: "u",
        sessionKey: "sess-key",
        turnId: "turn-1",
        llm: {} as HookContext["llm"],
        transcript: [],
        emit: () => {},
        log: () => {},
        abortSignal: new AbortController().signal,
        deadlineMs: 1_000,
      }) as HookContext,
    setPhase: (phase) => {
      phases.push(phase);
    },
    meta: { usage: { inputTokens: 1, outputTokens: 2, costUsd: 0 } },
    emittedAssistantBlocks: opts.blocks,
    commitRetryCount: 0,
    setAssistantText: (text) => {
      assistantTextRef.value = text;
    },
    rejectAllPendingAsks: (reason) => {
      rejectedReasons.push(reason);
    },
    getAssistantText: () => assistantTextRef.value,
  };

  return { ctx, sse, transcript, hooks, phases, assistantTextRef, rejectedReasons };
}

describe("CommitPipeline.commit", () => {
  it("writes assistant_text + turn_committed, fires afterCommit + afterTurnEnd + onTaskCheckpoint", async () => {
    const { ctx, sse, transcript, hooks, phases, assistantTextRef } = await makeCtx({
      blocks: [
        { type: "text", text: "Hello " },
        { type: "text", text: "world." },
      ],
    });
    const result = await commit(ctx);
    expect(result.finalText).toBe("Hello world.");
    expect(assistantTextRef.value).toBe("Hello world.");

    const entries = await transcript.readAll();
    const kinds = entries.map((e) => e.kind);
    expect(kinds).toContain("assistant_text");
    expect(kinds).toContain("turn_committed");

    const points = hooks.map((h) => h.point);
    expect(points).toContain("beforeCommit");
    expect(points).toContain("afterCommit");
    expect(points).toContain("afterTurnEnd");
    expect(points).toContain("onTaskCheckpoint");

    expect(phases).toEqual(["committing", "committed"]);
    const ends = sse.events.filter((e) => e.type === "turn_end");
    expect(ends.length).toBe(1);
    expect(ends[0]?.status).toBe("committed");
    expect(sse.finished).toBe(1);
  });

  it("skips assistant_text append when finalText is empty", async () => {
    const { ctx, transcript } = await makeCtx({ blocks: [] });
    await commit(ctx);
    const entries = await transcript.readAll();
    const kinds = entries.map((e) => e.kind);
    expect(kinds).not.toContain("assistant_text");
    expect(kinds).toContain("turn_committed");
  });

  it("beforeCommit block → throws, no transcript commits", async () => {
    const { ctx, transcript, phases } = await makeCtx({
      blocks: [{ type: "text", text: "x" }],
      blockBeforeCommit: "citation-gate",
    });
    await expect(commit(ctx)).rejects.toThrow(/beforeCommit blocked: citation-gate/);
    const entries = await transcript.readAll();
    const kinds = entries.map((e) => e.kind);
    expect(kinds).not.toContain("assistant_text");
    expect(kinds).not.toContain("turn_committed");
    // Phase went to "committing" but not "committed".
    expect(phases).toEqual(["committing"]);
  });

  it("onTaskCheckpoint payload includes toolNames + filesChanged", async () => {
    const { ctx, hooks } = await makeCtx({
      blocks: [
        { type: "text", text: "." },
        { type: "tool_use", id: "tu1", name: "FileWrite", input: { path: "a.ts" } },
        { type: "tool_use", id: "tu2", name: "Grep", input: { pattern: "x" } },
      ],
    });
    await commit(ctx);
    const cp = hooks.find((h) => h.point === "onTaskCheckpoint");
    const args = cp?.args as {
      toolNames: string[];
      filesChanged: string[];
      toolCallCount: number;
    };
    expect(args.toolNames).toEqual(["FileWrite", "Grep"]);
    expect(args.filesChanged).toEqual(["a.ts"]);
    expect(args.toolCallCount).toBe(2);
  });
});

describe("CommitPipeline.abort", () => {
  it("writes turn_aborted, fires onAbort + afterTurnEnd, rejects pending asks", async () => {
    const { ctx, sse, transcript, hooks, phases, rejectedReasons } = await makeCtx({
      blocks: [{ type: "text", text: "partial" }],
    });
    await abort(ctx, "user-cancelled");

    const entries = await transcript.readAll();
    const ab = entries.find((e) => e.kind === "turn_aborted");
    expect(ab).toBeDefined();
    expect((ab as { reason?: string }).reason).toBe("user-cancelled");

    const points = hooks.map((h) => h.point);
    expect(points).toContain("onAbort");
    expect(points).toContain("afterTurnEnd");

    expect(phases).toEqual(["aborted"]);
    expect(rejectedReasons).toEqual(["user-cancelled"]);

    const ends = sse.events.filter((e) => e.type === "turn_end");
    expect(ends[0]?.status).toBe("aborted");
    expect(ends[0]?.reason).toBe("user-cancelled");
  });
});

describe("CommitPipeline.collectFilesChanged", () => {
  it("scans FileWrite + FileEdit inputs only", () => {
    const blocks: LLMContentBlock[] = [
      { type: "text", text: "x" },
      { type: "tool_use", id: "1", name: "FileWrite", input: { path: "a.ts" } },
      { type: "tool_use", id: "2", name: "FileEdit", input: { path: "b.ts" } },
      { type: "tool_use", id: "3", name: "Grep", input: { path: "c.ts" } },
      { type: "tool_use", id: "4", name: "FileWrite", input: { path: "a.ts" } },
    ];
    expect(collectFilesChanged(blocks)).toEqual(["a.ts", "b.ts"]);
  });

  it("ignores tool_use without string path", () => {
    const blocks: LLMContentBlock[] = [
      { type: "tool_use", id: "1", name: "FileWrite", input: null },
      { type: "tool_use", id: "2", name: "FileWrite", input: { path: 42 } },
    ];
    expect(collectFilesChanged(blocks)).toEqual([]);
  });
});
