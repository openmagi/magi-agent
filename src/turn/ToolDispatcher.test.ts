/**
 * ToolDispatcher unit tests (R3 refactor).
 *
 * Stub hooks + tools + session, verify:
 *   - beforeToolUse → afterToolUse order
 *   - permission bypass skips beforeToolUse + emits audit
 *   - hook block path writes permission_denied transcript + tool_end
 *   - unknown tool path writes unknown_tool transcript + tool_end error
 *   - parallel execution (two tools run concurrently)
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServerResponse } from "node:http";
import {
  dispatch,
  UnknownToolLoopError,
  UNKNOWN_TOOL_LOOP_THRESHOLD,
  type ToolDispatchContext,
} from "./ToolDispatcher.js";
import type { LLMContentBlock } from "../transport/LLMClient.js";
import type { Session } from "../Session.js";
import { Transcript } from "../storage/Transcript.js";
import { SseWriter } from "../transport/SseWriter.js";
import type { HookContext } from "../hooks/types.js";
import { ControlEventLedger } from "../control/ControlEventLedger.js";
import { ControlRequestStore } from "../control/ControlRequestStore.js";
import type { ControlRequestRecord } from "../control/ControlEvents.js";

class FakeSse extends SseWriter {
  readonly events: Array<Record<string, unknown>> = [];
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
  override legacyFinish(): void {}
  override start(): void {}
  override end(): void {}
}

interface ToolRecord {
  name: string;
  calls: unknown[];
  contextCalls?: unknown[];
  onExecute?: () => Promise<void> | void;
  behaviour: "ok" | "throw" | "error-status" | "transient-then-ok";
  permission?: "read" | "write" | "execute" | "net" | "meta";
  dangerous?: boolean;
}

interface HookRecord {
  calls: Array<{ point: string; args: unknown }>;
  // Optional override: if set, returns a block action for beforeToolUse.
  blockReason?: string;
}

async function makeCtx(opts: {
  tools: ToolRecord[];
  permissionMode?: "default" | "plan" | "auto" | "bypass";
  blockReason?: string;
  askUserSelectedId?: string;
}): Promise<{
  ctx: ToolDispatchContext;
  sse: FakeSse;
  transcript: Transcript;
  auditEvents: Array<{ event: string; data?: Record<string, unknown> }>;
  hooks: HookRecord;
  session: Session;
  askUserCalls: unknown[];
}> {
  const workspaceRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "tool-dispatcher-"),
  );
  const sessionsDir = path.join(workspaceRoot, "sessions");
  await fs.mkdir(sessionsDir, { recursive: true });

  const sse = new FakeSse();
  const auditEvents: Array<{ event: string; data?: Record<string, unknown> }> = [];
  const askUserCalls: unknown[] = [];
  const hooks: HookRecord = { calls: [] };
  if (opts.blockReason !== undefined) hooks.blockReason = opts.blockReason;

  const transcript = new Transcript(sessionsDir, "sess-key");
  const controlEvents = new ControlEventLedger({
    rootDir: sessionsDir,
    sessionKey: "sess-key",
    transcript,
  });
  const controlRequests = new ControlRequestStore({ ledger: controlEvents });
  const waiters = new Map<string, (request: ControlRequestRecord) => void>();

  const toolRegistry = {
    list: () => [],
    resolve: (name: string) => {
      const t = opts.tools.find((x) => x.name === name);
      if (!t) return undefined;
      return {
        name: t.name,
        kind: "builtin" as const,
        description: t.name,
        permission: t.permission ?? "read",
        ...(t.dangerous ? { dangerous: true } : {}),
        inputSchema: { type: "object", properties: {} },
        execute: async (input: unknown, toolCtx: unknown) => {
          t.calls.push(input);
          t.contextCalls?.push(toolCtx);
          await t.onExecute?.();
          if (t.behaviour === "transient-then-ok") {
            if (t.calls.length === 1) {
              throw new Error("ETIMEDOUT");
            }
            return { status: "ok" as const, durationMs: 1, output: `${t.name}-output` };
          }
          if (t.behaviour === "throw") {
            throw new Error("boom");
          }
          if (t.behaviour === "error-status") {
            return {
              status: "error" as const,
              errorCode: "bad",
              errorMessage: "bad",
              durationMs: 1,
            };
          }
          return { status: "ok" as const, durationMs: 1, output: `${t.name}-output` };
        },
      };
    },
  };

  const agentStub = {
    config: {
      botId: "bot-test",
      userId: "user-test",
      workspaceRoot,
      model: "test-model",
    },
    hooks: {
      runPre: async (point: string, args: unknown) => {
        hooks.calls.push({ point, args });
        if (point === "beforeToolUse" && hooks.blockReason) {
          return { action: "block" as const, reason: hooks.blockReason };
        }
        return { action: "continue" as const, args };
      },
      runPost: async (point: string, args: unknown) => {
        hooks.calls.push({ point, args });
      },
      list: () => [],
    },
    tools: toolRegistry,
    auditLog: {
      append: async (
        event: string,
        _sk: string,
        _tid: string | undefined,
        data?: Record<string, unknown>,
      ) => {
        auditEvents.push({ event, ...(data !== undefined ? { data } : {}) });
      },
    },
  };

  const session = {
    meta: { sessionKey: "sess-key", channel: { type: "app", channelId: "general" } },
    transcript,
    controlEvents,
    controlRequests,
    resolveControlRequest: async (requestId: string, input: { decision: "approved" | "denied" | "answered"; updatedInput?: unknown; feedback?: string; answer?: string }) => {
      const resolved = await controlRequests.resolve(requestId, input);
      waiters.get(requestId)?.(resolved);
      return resolved;
    },
    waitForControlRequestResolution: async (requestId: string) => {
      const existing = (await controlRequests.project()).requests[requestId];
      if (!existing) throw new Error(`control request not found: ${requestId}`);
      if (existing.state !== "pending") return existing;
      return await new Promise<ControlRequestRecord>((resolve) => {
        waiters.set(requestId, resolve);
      });
    },
    agent: agentStub,
  } as unknown as Session;

  const ctx: ToolDispatchContext = {
    session,
    sse,
    turnId: "turn-test",
    permissionMode: opts.permissionMode ?? "default",
    buildHookContext: () =>
      ({
        botId: "bot-test",
        userId: "user-test",
        sessionKey: "sess-key",
        turnId: "turn-test",
        llm: {} as HookContext["llm"],
        transcript: [],
        emit: () => {},
        log: () => {},
        abortSignal: new AbortController().signal,
        deadlineMs: 1_000,
      }) as HookContext,
    stageAuditEvent: (event, data) => {
      auditEvents.push({ event, ...(data !== undefined ? { data } : {}) });
    },
    askUser: async (q) => {
      askUserCalls.push(q);
      return { selectedId: opts.askUserSelectedId ?? "deny" };
    },
  };

  return { ctx, sse, transcript, auditEvents, hooks, session, askUserCalls };
}

async function waitForPendingControlRequest(session: Session): Promise<ControlRequestRecord> {
  for (let i = 0; i < 50; i += 1) {
    const pending = await session.controlRequests.pending();
    if (pending[0]) return pending[0];
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("timed out waiting for pending control request");
}

async function waitForSseEvent(
  sse: FakeSse,
  predicate: (event: Record<string, unknown>) => boolean,
): Promise<Record<string, unknown>> {
  for (let i = 0; i < 50; i += 1) {
    const event = sse.events.find(predicate);
    if (event) return event;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("timed out waiting for SSE event");
}

function tu(id: string, name: string, input: unknown = {}): Extract<LLMContentBlock, { type: "tool_use" }> {
  return { type: "tool_use", id, name, input };
}

describe("ToolDispatcher.dispatch", () => {
  it("runs beforeToolUse → execute → afterToolUse in order", async () => {
    const tool: ToolRecord = { name: "Echo", calls: [], behaviour: "ok" };
    const { ctx, hooks } = await makeCtx({ tools: [tool] });
    const results = await dispatch(ctx, [tu("tu_1", "Echo", { x: 1 })]);
    expect(results.length).toBe(1);
    expect(results[0]?.isError).toBe(false);
    expect(tool.calls).toEqual([{ x: 1 }]);
    const points = hooks.calls.map((c) => c.point);
    expect(points).toEqual(["beforeToolUse", "afterToolUse"]);
  });

  it("passes the current user message through the tool context", async () => {
    const contextCalls: unknown[] = [];
    const tool: ToolRecord = {
      name: "Echo",
      calls: [],
      contextCalls,
      behaviour: "ok",
    };
    const { ctx } = await makeCtx({ tools: [tool] });
    const currentUserMessage = {
      text: "서브에이전트에게 선택한 파일 기준으로 처리시켜줘",
      receivedAt: 1_700_000_000_000,
      metadata: {
        systemPromptAddendum:
          "<kb-context>\n[file: guide.md]\n이 기준으로 작업해야 한다.\n</kb-context>",
      },
    };
    (ctx as typeof ctx & { currentUserMessage?: unknown }).currentUserMessage =
      currentUserMessage;

    const results = await dispatch(ctx, [tu("tu_1", "Echo", { x: 1 })]);

    expect(results[0]?.isError).toBe(false);
    expect(contextCalls[0]).toMatchObject({ currentUserMessage });
  });

  it("bypass mode skips beforeToolUse and emits permission_bypass audit", async () => {
    const tool: ToolRecord = { name: "Echo", calls: [], behaviour: "ok" };
    const { ctx, hooks, auditEvents } = await makeCtx({
      tools: [tool],
      permissionMode: "bypass",
    });
    await dispatch(ctx, [tu("tu_1", "Echo", { x: 1 })]);
    const preCalls = hooks.calls.filter((c) => c.point === "beforeToolUse");
    expect(preCalls.length).toBe(0);
    const bypass = auditEvents.find((e) => e.event === "permission_bypass");
    expect(bypass?.data?.toolName).toBe("Echo");
    // afterToolUse still observes.
    expect(hooks.calls.some((c) => c.point === "afterToolUse")).toBe(true);
  });

  it("bypass mode still denies security-critical shell commands before execution", async () => {
    const tool: ToolRecord = {
      name: "Bash",
      calls: [],
      behaviour: "ok",
      permission: "execute",
    };
    const { ctx, hooks, sse, transcript } = await makeCtx({
      tools: [tool],
      permissionMode: "bypass",
    });

    const results = await dispatch(ctx, [
      tu("tu_1", "Bash", { command: 'rm -rf "$(pwd)"' }),
    ]);

    expect(results[0]?.isError).toBe(true);
    expect(results[0]?.content).toContain("destructive rm -rf");
    expect(tool.calls.length).toBe(0);
    expect(hooks.calls.some((c) => c.point === "beforeToolUse")).toBe(false);
    const ends = sse.events.filter((e) => e.type === "tool_end");
    expect(ends[0]?.status).toBe("permission_denied");
    const entries = await transcript.readAll();
    const res = entries.find((e) => e.kind === "tool_result");
    expect((res as { status?: string }).status).toBe("permission_denied");
  });

  it("hook block → permission_denied tool_end + transcript, tool NOT executed", async () => {
    const tool: ToolRecord = { name: "Bash", calls: [], behaviour: "ok" };
    const { ctx, sse, transcript } = await makeCtx({
      tools: [tool],
      blockReason: "forbidden-tool",
    });
    const results = await dispatch(ctx, [tu("tu_1", "Bash")]);
    expect(results[0]?.isError).toBe(true);
    expect(results[0]?.content).toContain("forbidden-tool");
    expect(tool.calls.length).toBe(0);
    const ends = sse.events.filter((e) => e.type === "tool_end");
    expect(ends[0]?.status).toBe("permission_denied");
    const entries = await transcript.readAll();
    const res = entries.find((e) => e.kind === "tool_result");
    expect(res).toBeDefined();
    expect((res as { status?: string }).status).toBe("permission_denied");
  });

  it("dangerous tool asks through durable control request and executes updated input", async () => {
    const tool: ToolRecord = {
      name: "Bash",
      calls: [],
      behaviour: "ok",
      permission: "execute",
      dangerous: true,
    };
    const { ctx, session, sse, transcript } = await makeCtx({ tools: [tool] });

    const run = dispatch(ctx, [tu("tu_1", "Bash", { command: "npm test" })]);
    const request = await waitForPendingControlRequest(session);
    expect(request.kind).toBe("tool_permission");
    expect(request.proposedInput).toEqual({ command: "npm test" });
    await expect(waitForSseEvent(sse, (event) => event.type === "control_event")).resolves.toBeTruthy();

    await session.resolveControlRequest(request.requestId, {
      decision: "approved",
      updatedInput: { command: "npm run lint" },
    });

    const results = await run;
    expect(results[0]?.isError).toBe(false);
    expect(tool.calls).toEqual([{ command: "npm run lint" }]);
    const entries = await transcript.readAll();
    const call = entries.find((entry) => entry.kind === "tool_call");
    expect(call).toMatchObject({
      kind: "tool_call",
      input: { command: "npm run lint" },
    });
  });

  it("unknown tool → unknown_tool transcript + error tool_end", async () => {
    const { ctx, sse, transcript } = await makeCtx({ tools: [] });
    const results = await dispatch(ctx, [tu("tu_1", "NoSuchTool")]);
    expect(results[0]?.isError).toBe(true);
    const ends = sse.events.filter((e) => e.type === "tool_end");
    expect(ends[0]?.status).toBe("error");
    const entries = await transcript.readAll();
    const res = entries.find((e) => e.kind === "tool_result");
    expect((res as { status?: string }).status).toBe("unknown_tool");
  });

  it("tool that throws → maps to tool_threw error result", async () => {
    const tool: ToolRecord = { name: "Bad", calls: [], behaviour: "throw" };
    const { ctx, sse } = await makeCtx({ tools: [tool] });
    const results = await dispatch(ctx, [tu("tu_1", "Bad")]);
    expect(results[0]?.isError).toBe(true);
    const ends = sse.events.filter((e) => e.type === "tool_end");
    expect(ends[0]?.status).toBe("error");
  });

  it("retries a transient failure once for safe tools", async () => {
    const tool: ToolRecord = {
      name: "FetchContext",
      calls: [],
      behaviour: "transient-then-ok",
      permission: "read",
    };
    const { ctx, sse } = await makeCtx({ tools: [tool] });

    const results = await dispatch(ctx, [tu("tu_1", "FetchContext")]);

    expect(results[0]?.isError).toBe(false);
    expect(tool.calls).toHaveLength(2);
    const retries = sse.events.filter((e) => e.type === "retry");
    expect(retries).toHaveLength(1);
    expect(retries[0]).toMatchObject({
      type: "retry",
      toolUseId: "tu_1",
      toolName: "FetchContext",
      retryNo: 1,
    });
  });

  it("does not auto-retry dangerous tools", async () => {
    const tool: ToolRecord = {
      name: "DangerousNet",
      calls: [],
      behaviour: "transient-then-ok",
      permission: "net",
      dangerous: true,
    };
    const { ctx, sse } = await makeCtx({ tools: [tool], permissionMode: "bypass" });

    const results = await dispatch(ctx, [tu("tu_1", "DangerousNet")]);

    expect(results[0]?.isError).toBe(true);
    expect(tool.calls).toHaveLength(1);
    const retries = sse.events.filter((e) => e.type === "retry");
    expect(retries).toHaveLength(0);
  });

  it("runs multiple tool_use blocks in parallel", async () => {
    const a: ToolRecord = { name: "A", calls: [], behaviour: "ok" };
    const b: ToolRecord = { name: "B", calls: [], behaviour: "ok" };
    const { ctx } = await makeCtx({ tools: [a, b] });
    const results = await dispatch(ctx, [tu("tu_a", "A"), tu("tu_b", "B")]);
    expect(results.length).toBe(2);
    expect(a.calls.length).toBe(1);
    expect(b.calls.length).toBe(1);
  });

  it("serializes mutating tool_use blocks while keeping read-only tools concurrent", async () => {
    let activeWrites = 0;
    let maxActiveWrites = 0;
    const events: string[] = [];
    const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
    const makeWrite = (name: string): ToolRecord => ({
      name,
      calls: [],
      behaviour: "ok",
      permission: "write",
      onExecute: async () => {
        activeWrites += 1;
        maxActiveWrites = Math.max(maxActiveWrites, activeWrites);
        events.push(`${name}:start`);
        await wait(15);
        events.push(`${name}:end`);
        activeWrites -= 1;
      },
    });
    const writeA = makeWrite("WriteA");
    const writeB = makeWrite("WriteB");
    const readA: ToolRecord = { name: "ReadA", calls: [], behaviour: "ok", permission: "read" };
    const readB: ToolRecord = { name: "ReadB", calls: [], behaviour: "ok", permission: "read" };
    const { ctx } = await makeCtx({
      tools: [readA, readB, writeA, writeB],
      permissionMode: "bypass",
    });

    const results = await dispatch(ctx, [
      tu("read_a", "ReadA"),
      tu("read_b", "ReadB"),
      tu("write_a", "WriteA"),
      tu("write_b", "WriteB"),
    ]);

    expect(results.map((r) => r.isError)).toEqual([false, false, false, false]);
    expect(readA.calls).toHaveLength(1);
    expect(readB.calls).toHaveLength(1);
    expect(writeA.calls).toHaveLength(1);
    expect(writeB.calls).toHaveLength(1);
    expect(maxActiveWrites).toBe(1);
    expect(events).toEqual(["WriteA:start", "WriteA:end", "WriteB:start", "WriteB:end"]);
  });
});

describe("ToolDispatcher unknown-tool loop guard (Gap §11.3)", () => {
  it("enriches unknown_tool output with 'Available tools: ...'", async () => {
    const ok: ToolRecord = { name: "Echo", calls: [], behaviour: "ok" };
    const { ctx } = await makeCtx({ tools: [ok] });
    // Inject a fake list() on the registry by re-wiring session.agent.tools.
    (ctx.session.agent as unknown as {
      tools: { list: () => Array<{ name: string }>; resolve: (n: string) => unknown };
    }).tools = {
      list: () => [{ name: "Echo" }, { name: "Bash" }],
      resolve: (n: string) => (n === "Echo" ? {
        name: "Echo",
        execute: async () => ({ status: "ok", durationMs: 1, output: "ok" }),
      } : undefined),
    };
    const counter = { get: () => n, inc: () => ++n };
    let n = 0;
    const out = await dispatch(
      { ...ctx, unknownToolCounter: counter },
      [tu("u1", "NoSuchTool")],
    );
    expect(out[0]?.content).toContain("Unknown tool: NoSuchTool");
    expect(out[0]?.content).toContain("Available tools:");
    expect(out[0]?.content).toContain("Echo");
    expect(out[0]?.content).toContain("Bash");
  });

  it("counter increments on every unknown dispatch", async () => {
    const { ctx } = await makeCtx({ tools: [] });
    let n = 0;
    const counter = { get: () => n, inc: () => ++n };
    await dispatch({ ...ctx, unknownToolCounter: counter }, [tu("u1", "X")]);
    await dispatch({ ...ctx, unknownToolCounter: counter }, [tu("u2", "Y")]);
    await dispatch({ ...ctx, unknownToolCounter: counter }, [tu("u3", "Z")]);
    expect(n).toBe(3);
  });

  it("throws UnknownToolLoopError once the threshold is reached", async () => {
    const { ctx, sse, auditEvents } = await makeCtx({ tools: [] });
    let n = 0;
    const counter = { get: () => n, inc: () => ++n };
    // 10 unknown tool_use blocks in a single batch → exactly at threshold.
    const batch = Array.from({ length: UNKNOWN_TOOL_LOOP_THRESHOLD }).map(
      (_, i) => tu(`u${i}`, "NoSuchTool"),
    );
    await expect(
      dispatch({ ...ctx, unknownToolCounter: counter }, batch),
    ).rejects.toBeInstanceOf(UnknownToolLoopError);
    expect(n).toBe(UNKNOWN_TOOL_LOOP_THRESHOLD);
    const textDeltas = sse.events.filter((e) => e.type === "text_delta");
    expect(textDeltas.length).toBe(1);
    const delta = textDeltas[0]?.delta as string | undefined;
    expect(delta).toContain("할루시네이션");
    const aborted = auditEvents.find((e) => e.event === "unknown_tool_loop");
    expect(aborted?.data?.count).toBe(UNKNOWN_TOOL_LOOP_THRESHOLD);
  });

  it("below threshold does NOT throw", async () => {
    const { ctx } = await makeCtx({ tools: [] });
    let n = 0;
    const counter = { get: () => n, inc: () => ++n };
    const batch = Array.from({ length: UNKNOWN_TOOL_LOOP_THRESHOLD - 1 }).map(
      (_, i) => tu(`u${i}`, "NoSuchTool"),
    );
    const res = await dispatch({ ...ctx, unknownToolCounter: counter }, batch);
    expect(res.length).toBe(UNKNOWN_TOOL_LOOP_THRESHOLD - 1);
    expect(res.every((r) => r.isError)).toBe(true);
  });

  it("counter accumulates across multiple dispatch calls in one turn", async () => {
    const { ctx } = await makeCtx({ tools: [] });
    let n = 0;
    const counter = { get: () => n, inc: () => ++n };
    // 4 unknown dispatches per batch × 3 batches = 12 > threshold.
    const mkBatch = (prefix: string): Array<ReturnType<typeof tu>> =>
      Array.from({ length: 4 }).map((_, i) => tu(`${prefix}${i}`, "Ghost"));
    await dispatch({ ...ctx, unknownToolCounter: counter }, mkBatch("a"));
    await dispatch({ ...ctx, unknownToolCounter: counter }, mkBatch("b"));
    await expect(
      dispatch({ ...ctx, unknownToolCounter: counter }, mkBatch("c")),
    ).rejects.toBeInstanceOf(UnknownToolLoopError);
    expect(n).toBe(12);
  });
});
