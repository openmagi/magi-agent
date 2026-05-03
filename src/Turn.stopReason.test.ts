/**
 * Turn stop-reason taxonomy + output-token recovery tests (T1-04 + T1-05).
 *
 * Exercises the switch introduced around the old binary
 * `tool_use vs else` branch at `Turn.execute()`, driving the loop with
 * a scripted mock LLMClient that emits pre-seeded stop_reasons.
 *
 * Coverage:
 *   1. end_turn           — normal finalise, one LLM call.
 *   2. tool_use           — tools dispatched, then end_turn.
 *   3. max_tokens ×1      — one recovery fires, text concatenated.
 *   4. max_tokens ×2      — two recoveries fire.
 *   5. max_tokens ×4      — third recovery issued, fourth NOT made,
 *                           output_recovery_exhausted audit emitted.
 *   6. refusal            — rule_check_violation audit event, turn
 *                           finalises with the refusal text visible.
 *   7. stop_sequence / pause_turn / unknown — each handled distinctly.
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServerResponse } from "node:http";
import {
  Turn,
  MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
  classifyStopReason,
} from "./Turn.js";
import type {
  LLMEvent,
  LLMStreamRequest,
} from "./transport/LLMClient.js";
import type { UserMessage } from "./util/types.js";
import { Transcript } from "./storage/Transcript.js";
import type { AuditLog } from "./storage/AuditLog.js";
import { SseWriter } from "./transport/SseWriter.js";
import type { Session } from "./Session.js";
import type { RouteDecision } from "./routing/types.js";

// ─────────────────────────────────────────────────────────────────────
// Scripted stream shape: one entry per iteration the model would run.
// ─────────────────────────────────────────────────────────────────────

interface ScriptedTurn {
  blocks: Array<
    | { type: "text"; text: string }
    | { type: "thinking"; thinking: string; signature: string }
    | { type: "tool_use"; id: string; name: string; input: unknown }
  >;
  stopReason:
    | "end_turn"
    | "tool_use"
    | "max_tokens"
    | "stop_sequence"
    | "refusal"
    | "pause_turn"
    | null
    | "mystery_reason";
}

function* scriptedEvents(turn: ScriptedTurn): Generator<LLMEvent, void, void> {
  let idx = 0;
  for (const b of turn.blocks) {
    if (b.type === "text") {
      yield { kind: "text_delta", blockIndex: idx, delta: b.text };
    } else if (b.type === "thinking") {
      yield { kind: "thinking_delta", blockIndex: idx, delta: b.thinking };
      yield { kind: "thinking_signature", blockIndex: idx, signature: b.signature };
    } else {
      yield { kind: "tool_use_start", blockIndex: idx, id: b.id, name: b.name };
      yield {
        kind: "tool_use_input_delta",
        blockIndex: idx,
        partial: JSON.stringify(b.input ?? {}),
      };
    }
    yield { kind: "block_stop", blockIndex: idx };
    idx += 1;
  }
  yield {
    kind: "message_end",
    // Cast so the unknown-case test can inject an intentionally novel
    // stop_reason wire value.
    stopReason: turn.stopReason as "end_turn",
    usage: { inputTokens: 5, outputTokens: 5 },
  };
}

class ScriptedLLM {
  public readonly calls: LLMStreamRequest[] = [];
  constructor(private readonly script: ScriptedTurn[]) {}

  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    this.calls.push(req);
    const next = this.script.shift();
    if (!next) {
      throw new Error(
        `ScriptedLLM out of scripted turns (call #${this.calls.length})`,
      );
    }
    for (const evt of scriptedEvents(next)) yield evt;
  }
}

// ─────────────────────────────────────────────────────────────────────
// Fake SseWriter — records events but never touches a socket.
// ─────────────────────────────────────────────────────────────────────

class FakeSse extends SseWriter {
  readonly agentEvents: unknown[] = [];
  constructor() {
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }
  override agent(event: unknown): void {
    this.agentEvents.push(event);
  }
  override legacyDelta(): void {
    /* no-op */
  }
  override legacyFinish(): void {
    /* no-op */
  }
  override start(): void {
    /* no-op */
  }
  override end(): void {
    /* no-op */
  }
}

// ─────────────────────────────────────────────────────────────────────
// Minimal Agent / Session stubs. We only implement the surface Turn
// actually touches during execute().
// ─────────────────────────────────────────────────────────────────────

interface ToolEcho {
  name: string;
  calls: Array<{ id: string; input: unknown }>;
}

interface Fixture {
  turn: Turn;
  llm: ScriptedLLM;
  sse: FakeSse;
  auditEvents: Array<{ event: string; data?: Record<string, unknown> }>;
  toolCalls: ToolEcho[];
  workspaceRoot: string;
}

async function makeFixture(
  script: ScriptedTurn[],
  opts: {
    pendingInjections?: boolean[];
    toolNames?: string[];
    model?: string;
    router?: { resolve: (input: unknown) => Promise<RouteDecision> };
  } = {},
): Promise<Fixture> {
  const workspaceRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "turn-stopreason-"),
  );
  const sessionsDir = path.join(workspaceRoot, "core-agent", "sessions");
  await fs.mkdir(sessionsDir, { recursive: true });

  const llm = new ScriptedLLM(script);
  const auditEvents: Array<{
    event: string;
    data?: Record<string, unknown>;
  }> = [];

  const tools: ToolEcho[] = (opts.toolNames ?? []).map((n) => ({
    name: n,
    calls: [],
  }));

  const hooks = {
    runPre: async (_point: string, args: unknown) => ({
      action: "continue" as const,
      args,
    }),
    runPost: async () => {},
    list: () => [],
  };

  const toolRegistry = {
    list: () =>
      tools.map((t) => ({
        name: t.name,
        kind: "builtin" as const,
        description: `${t.name} test tool`,
        inputSchema: { type: "object", properties: {} },
        tags: [],
        execute: async () => ({
          status: "ok" as const,
          durationMs: 1,
          output: "ok",
        }),
      })),
    resolve: (name: string) => {
      const t = tools.find((tt) => tt.name === name);
      if (!t) return undefined;
      return {
        name: t.name,
        kind: "builtin" as const,
        description: `${t.name}`,
        inputSchema: { type: "object", properties: {} },
        execute: async (input: unknown) => {
          t.calls.push({
            id: `${t.name}-call-${t.calls.length}`,
            input,
          });
          return {
            status: "ok" as const,
            durationMs: 1,
            output: "ok",
          };
        },
      };
    },
  };

  const workspace = {
    loadIdentity: async () => ({}),
  };

  const intent = {
    classify: async () => ["general"],
  };

  // Capture audit events instead of writing to disk.
  const auditLog: Pick<AuditLog, "append"> = {
    append: async (
      event: string,
      _sessionKey: string,
      _turnId: string | undefined,
      data?: Record<string, unknown>,
    ) => {
      auditEvents.push({ event, ...(data !== undefined ? { data } : {}) });
    },
  };

  const config = {
    botId: "bot-stop-reason",
    userId: "user-stop-reason",
    workspaceRoot,
    gatewayToken: "test",
    apiProxyUrl: "http://localhost",
    chatProxyUrl: "http://localhost",
    redisUrl: "redis://localhost",
    model: opts.model ?? "claude-opus-4-7",
  };

  // T1-02 compaction_boundary — ContextEngine stub that never
  // compacts and returns a simple text-only replay. Enough for these
  // tests to exercise stop-reason branches.
  const contextEngine = {
    maybeCompact: async () => {},
    buildMessagesFromTranscript: () => [],
  };

  const agentStub = {
    config,
    router: opts.router ?? null,
    hooks,
    tools: toolRegistry,
    intent,
    workspace,
    auditLog,
    llm,
    sessionsDir,
    contextEngine,
  };

  const sessionMeta = {
    sessionKey: "agent:main:app:general:1",
    botId: config.botId,
    channel: { type: "app" as const, channelId: "general" },
    createdAt: Date.now(),
    lastActivityAt: Date.now(),
  };

  const transcript = new Transcript(sessionsDir, sessionMeta.sessionKey);
  let pendingInjectionCheck = 0;

  const sessionStub = {
    meta: sessionMeta,
    transcript,
    agent: agentStub,
    // T1-06 budget gate — always under budget for these tests.
    budgetExceeded: () => ({ exceeded: false as const }),
    budgetStats: () => ({
      turns: 0,
      inputTokens: 0,
      outputTokens: 0,
      costUsd: 0,
    }),
    recordTurnUsage: () => {},
    maxTurns: 50,
    maxCostUsd: 10,
    setActiveSse: () => {},
    hasPendingInjections: () =>
      opts.pendingInjections?.[pendingInjectionCheck++] ?? false,
  };

  const userMessage: UserMessage = {
    text: "say something long please",
    receivedAt: Date.now(),
  };

  const sse = new FakeSse();
  const turn = new Turn(
    sessionStub as unknown as Session,
    userMessage,
    "01JXTESTTURN",
    sse,
    "direct",
  );

  return { turn, llm, sse, auditEvents, toolCalls: tools, workspaceRoot };
}

// ─────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────

describe("classifyStopReason", () => {
  it("maps each canonical wire value to itself", () => {
    for (const v of [
      "end_turn",
      "tool_use",
      "stop_sequence",
      "max_tokens",
      "refusal",
      "pause_turn",
    ] as const) {
      expect(classifyStopReason(v)).toBe(v);
    }
  });
  it("null / unexpected string → unknown", () => {
    expect(classifyStopReason(null)).toBe("unknown");
    expect(classifyStopReason(undefined)).toBe("unknown");
    expect(classifyStopReason("novel_reason")).toBe("unknown");
  });
});

describe("Turn.execute() stop-reason taxonomy", () => {
  it("MAX_OUTPUT_TOKENS_RECOVERY_LIMIT is 3 (CC parity)", () => {
    expect(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT).toBe(3);
  });

  it("end_turn → finalises with a single LLM call", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      { blocks: [{ type: "text", text: "hello." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(1);
    expect(turn.meta.stopReason).toBe("end_turn");
    expect(turn.getRecoveryAttempt()).toBe(0);
    const events = auditEvents.map((e) => e.event);
    expect(events).not.toContain("output_recovery");
    expect(events).not.toContain("rule_check_violation");
  });

  it("injects the current routed model identity into the LLM call", async () => {
    const decision: RouteDecision = {
      profileId: "premium",
      tier: "DEEP",
      provider: "openai",
      model: "gpt-5.5",
      supportsTools: true,
      supportsImages: true,
      reason: "premium DEEP",
      classifierUsed: true,
      classifierModel: "claude-sonnet-4-6",
      classifierRaw: "DEEP",
      confidence: "classifier",
    };
    const { turn, llm } = await makeFixture(
      [{ blocks: [{ type: "text", text: "hello." }], stopReason: "end_turn" }],
      {
        model: "clawy-smart-router/auto",
        router: { resolve: async () => decision },
      },
    );

    await turn.execute();

    const messages = llm.calls[0]?.messages ?? [];
    const identityContent = messages[0]?.content;
    const identityText = Array.isArray(identityContent) && identityContent[0]?.type === "text"
      ? identityContent[0].text
      : "";
    expect(identityText).toContain("<runtime_model_identity hidden=\"true\">");
    expect(identityText).toContain("router: Premium Router");
    expect(identityText).toContain("answering_model: openai/gpt-5.5");
    expect(identityText).toContain("classifier_model: claude-sonnet-4-6");
    expect(messages.at(-1)?.content).toBe("say something long please");
  });

  it("pending injection deferral clears prior visible draft and commits only the resumed answer", async () => {
    const { turn, llm, sse } = await makeFixture(
      [
        { blocks: [{ type: "text", text: "draft answer. " }], stopReason: "end_turn" },
        { blocks: [{ type: "text", text: "resumed answer." }], stopReason: "end_turn" },
      ],
      { pendingInjections: [true, false] },
    );

    await turn.execute();
    const commit = await turn.commit();

    expect(llm.calls.length).toBe(2);
    expect(commit.finalText).toBe("resumed answer.");
    expect(
      sse.agentEvents.some(
        (event) =>
          typeof event === "object" &&
          event !== null &&
          (event as { type?: string }).type === "response_clear",
      ),
    ).toBe(true);
  });

  it("falls back to a text model instead of committing empty output after empty-response retries", async () => {
    const geminiDecision: RouteDecision = {
      profileId: "premium",
      tier: "DEEP",
      provider: "google",
      model: "gemini-3.1-pro-preview",
      supportsTools: true,
      supportsImages: true,
      reason: "premium deep",
      classifierUsed: true,
      classifierModel: "claude-sonnet-4-6",
      classifierRaw: "DEEP",
      confidence: "classifier",
    };
    const empty = { blocks: [], stopReason: "end_turn" as const };
    const { turn, llm, auditEvents } = await makeFixture(
      [
        empty,
        empty,
        empty,
        empty,
        { blocks: [{ type: "text", text: "Recovered with visible text." }], stopReason: "end_turn" },
      ],
      {
        model: "big-dic-router/auto",
        router: { resolve: async () => geminiDecision },
      },
    );

    await turn.execute();
    const commit = await turn.commit();

    expect(commit.finalText).toBe("Recovered with visible text.");
    expect(llm.calls.map((call) => call.model)).toEqual([
      "gemini-3.1-pro-preview",
      "gemini-3.1-pro-preview",
      "gemini-3.1-pro-preview",
      "gemini-3.1-pro-preview",
      "claude-haiku-4-5-20251001",
    ]);
    expect(auditEvents.some((event) => event.event === "empty_response_fallback")).toBe(true);
    expect(turn.meta.stopReason).toBe("end_turn");
  });

  it("does not treat a long complete answer ending with an emoji as truncated", async () => {
    const answer =
      "접속 실패 원인과 확인 방법을 정리했습니다.\n\n" +
      "가능한 원인\n" +
      "1. 서버 프로세스가 중단된 상태일 수 있습니다.\n" +
      "2. 배포 과정에서 포트가 바뀌었을 수 있습니다.\n" +
      "3. 방화벽이나 보안그룹에서 외부 접근이 막혔을 수 있습니다.\n" +
      "4. 베타 프로세스가 크래시 후 자동 복구되지 않았을 수 있습니다.\n\n" +
      "서버에서 `netstat -tlnp | grep 18427` 또는 `docker ps`로 상태를 확인해주시고, " +
      "서비스를 다시 올린 뒤 알려주시면 이어서 UX와 보안 관점까지 점검하겠습니다. 🔧";
    const { turn, llm } = await makeFixture([
      { blocks: [{ type: "text", text: answer }], stopReason: "end_turn" },
      { blocks: [{ type: "text", text: "이전 응답은 잘리지 않았습니다. 핵심 내용을 다시 정리드리면..." }], stopReason: "end_turn" },
    ]);

    await turn.execute();
    const commit = await turn.commit();

    expect(llm.calls.length).toBe(1);
    expect(commit.finalText).toBe(answer);
  });

  it("does not treat a long complete answer ending with a mention as truncated", async () => {
    const answer =
      "테스트 진행 상태를 정리했습니다.\n\n" +
      "현재 확인된 내용은 로그인 전 랜딩 페이지, 개인정보 안내, 로그인 패널, 동의 체크박스, " +
      "Next.js 기반 SPA 구조입니다. 로그인 이후 흐름은 실제 계정 정보와 브라우저 세션이 필요합니다.\n\n" +
      "요청하신 항목은 대기 상태로 두고, 계정 정보가 제공되면 질문 응답, 프로파일 생성, 딜 검토, 저장 복원, " +
      "반응형 확인까지 이어서 진행하겠습니다.\n\n" +
      "@donggun_jung";
    const { turn, llm } = await makeFixture([
      { blocks: [{ type: "text", text: answer }], stopReason: "end_turn" },
      { blocks: [{ type: "text", text: "이전 응답은 잘리지 않았습니다." }], stopReason: "end_turn" },
    ]);

    await turn.execute();
    const commit = await turn.commit();

    expect(llm.calls.length).toBe(1);
    expect(commit.finalText).toBe(answer);
  });

  it("continues a long answer that appears to end mid-sentence", async () => {
    const partial =
      "긴 분석을 작성하는 중입니다. ".repeat(12) +
      "마지막으로 서버 프로세스가 계속 내려가는 경우에는 배포 로그와 런타임 로그를 함께 확인해야 하며 원인은";
    const continuation = " 프로세스 크래시 또는 포트 바인딩 실패일 가능성이 큽니다.";
    const { turn, llm } = await makeFixture([
      { blocks: [{ type: "text", text: partial }], stopReason: "end_turn" },
      { blocks: [{ type: "text", text: continuation }], stopReason: "end_turn" },
    ]);

    await turn.execute();
    const commit = await turn.commit();

    expect(llm.calls.length).toBe(2);
    expect(llm.calls[1]?.messages.at(-1)?.content).toBe(
      "Your response was cut off mid-sentence. Continue from where you left off.",
    );
    expect(commit.finalText).toBe(partial + continuation);
  });

  it("tool_use → runs tools then terminates on end_turn", async () => {
    const { turn, llm, toolCalls } = await makeFixture(
      [
        {
          blocks: [
            {
              type: "tool_use",
              id: "tool_01",
              name: "Echo",
              input: { msg: "hi" },
            },
          ],
          stopReason: "tool_use",
        },
        { blocks: [{ type: "text", text: "done." }], stopReason: "end_turn" },
      ],
      { toolNames: ["Echo"] },
    );
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    const echo = toolCalls.find((t) => t.name === "Echo");
    expect(echo?.calls.length).toBe(1);
    expect(turn.getRecoveryAttempt()).toBe(0);
  });

  it("replays assistant text before tool_use when model streams text after tool_use", async () => {
    const { turn, llm } = await makeFixture(
      [
        {
          blocks: [
            {
              type: "tool_use",
              id: "tool_01",
              name: "Echo",
              input: { msg: "hi" },
            },
            { type: "text", text: "잠시만요." },
          ],
          stopReason: "tool_use",
        },
        { blocks: [{ type: "text", text: "done." }], stopReason: "end_turn" },
      ],
      { toolNames: ["Echo"] },
    );

    await turn.execute();

    const assistantWithToolUse = llm.calls[1]?.messages.find(
      (message) =>
        message.role === "assistant" &&
        Array.isArray(message.content) &&
        message.content.some((block) => block.type === "tool_use"),
    );
    expect(assistantWithToolUse).toBeDefined();
    if (!assistantWithToolUse || !Array.isArray(assistantWithToolUse.content)) {
      return;
    }
    expect(assistantWithToolUse.content.map((block) => block.type)).toEqual([
      "text",
      "tool_use",
    ]);
  });

  it("max_tokens (1×) → recovery fires, second call concatenates", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      { blocks: [{ type: "text", text: "part-1 " }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "part-2." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    expect(turn.meta.stopReason).toBe("max_tokens_recovered");
    expect(turn.getRecoveryAttempt()).toBe(1);
    // Recovery call must include the "Continue." nudge as the last msg.
    const secondCall = llm.calls[1]!;
    const lastMsg = secondCall.messages[secondCall.messages.length - 1];
    expect(lastMsg?.role).toBe("user");
    expect(lastMsg?.content).toBe("Continue.");
    const recs = auditEvents.filter((e) => e.event === "output_recovery");
    expect(recs.length).toBe(1);
    expect(recs[0]?.data?.recoveryAttempt).toBe(1);
  });

  it("max_tokens (2×) → two recoveries fire", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      { blocks: [{ type: "text", text: "A" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "B" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "C." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(3);
    expect(turn.getRecoveryAttempt()).toBe(2);
    expect(
      auditEvents.filter((e) => e.event === "output_recovery").length,
    ).toBe(2);
  });

  it("max_tokens (4×) → third recovery made, fourth refused with exhausted audit", async () => {
    // Four calls then a sentinel; if the impl made a fifth call the
    // exhausted audit would not fire. We assert llm.calls.length=4.
    const { turn, llm, auditEvents } = await makeFixture([
      { blocks: [{ type: "text", text: "1" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "2" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "3" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "4" }], stopReason: "max_tokens" },
      { blocks: [{ type: "text", text: "!" }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(4);
    expect(turn.getRecoveryAttempt()).toBe(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT);
    const exhausted = auditEvents.filter(
      (e) => e.event === "output_recovery_exhausted",
    );
    expect(exhausted.length).toBe(1);
    expect(exhausted[0]?.data?.limit).toBe(MAX_OUTPUT_TOKENS_RECOVERY_LIMIT);
    // "1" + "2" + "3" + "4" = 4 chars.
    expect(exhausted[0]?.data?.finalLength).toBe(4);
  });

  it("refusal → stages rule_check_violation audit and finalises", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      {
        blocks: [{ type: "text", text: "I can't help with that." }],
        stopReason: "refusal",
      },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(1);
    const refusal = auditEvents.find(
      (e) => e.event === "rule_check_violation",
    );
    expect(refusal).toBeDefined();
    expect(refusal?.data?.reason).toBe("model_refusal");
    expect(refusal?.data?.stop_reason).toBe("refusal");
  });

  it("stop_sequence → finalises normally, no audit events", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      {
        blocks: [{ type: "text", text: "halted by stop seq" }],
        stopReason: "stop_sequence",
      },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(1);
    const noisy = auditEvents.filter(
      (e) =>
        e.event === "rule_check_violation" ||
        e.event === "output_recovery" ||
        e.event === "stop_reason_unknown",
    );
    expect(noisy.length).toBe(0);
  });

  it("pause_turn → treated as continuation (shares recovery budget)", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      {
        blocks: [{ type: "text", text: "paused..." }],
        stopReason: "pause_turn",
      },
      { blocks: [{ type: "text", text: "resumed." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    expect(turn.getRecoveryAttempt()).toBe(1);
    const rec = auditEvents.find((e) => e.event === "output_recovery");
    expect(rec?.data?.stop_reason).toBe("pause_turn");
  });

  it("unknown stop_reason → stop_reason_unknown audit, finalises", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      {
        blocks: [{ type: "text", text: "huh" }],
        stopReason: "mystery_reason",
      },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(1);
    const unk = auditEvents.find((e) => e.event === "stop_reason_unknown");
    expect(unk).toBeDefined();
    expect(unk?.data?.raw).toBe("mystery_reason");
  });

  // [codex gate2 P2] Recovery must not push an unresolved tool_use to
  // the model — Anthropic requires every assistant tool_use to be
  // followed by a matching tool_result. A truncated tool_use mid-
  // max_tokens must be stripped before the Continue. nudge.
  it("max_tokens with trailing tool_use → drops tool_use, recovery succeeds", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      {
        blocks: [
          { type: "text", text: "Let me call " },
          { type: "tool_use", id: "tu_partial", name: "Bash", input: {} },
        ],
        stopReason: "max_tokens",
      },
      { blocks: [{ type: "text", text: "done." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    // The recovery LLM call's messages must not contain an unresolved
    // tool_use in the assistant message.
    const secondCall = llm.calls[1]!;
    const assistantMsgs = secondCall.messages.filter(
      (m) => m.role === "assistant",
    );
    for (const msg of assistantMsgs) {
      if (Array.isArray(msg.content)) {
        expect(
          msg.content.some((b: { type: string }) => b.type === "tool_use"),
        ).toBe(false);
      }
    }
    const dropAudit = auditEvents.find(
      (e) => e.event === "output_recovery_drop_unresolved_tool_use",
    );
    expect(dropAudit).toBeDefined();
    expect(dropAudit?.data?.dropped).toBe(1);
  });

  // T4-18: thinking blocks must be preserved across iterations with
  // their signatures so Anthropic accepts the replayed trajectory.
  it("thinking block preserved across iterations with signature", async () => {
    const { turn, llm } = await makeFixture([
      {
        blocks: [
          { type: "thinking", thinking: "reasoning step 1", signature: "sig-abc-123" },
          { type: "tool_use", id: "tu_1", name: "Probe", input: { x: 1 } },
        ],
        stopReason: "tool_use",
      },
      { blocks: [{ type: "text", text: "done." }], stopReason: "end_turn" },
    ], { toolNames: ["Probe"] });
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    // The second LLM call's messages must include the prior assistant
    // message, which in turn must contain the thinking block with its
    // signature byte-identical to what the scripted stream emitted.
    const secondCall = llm.calls[1]!;
    const priorAssistant = secondCall.messages.find((m) => m.role === "assistant");
    expect(priorAssistant).toBeDefined();
    expect(Array.isArray(priorAssistant?.content)).toBe(true);
    const thinkingBlock = (priorAssistant?.content as Array<{ type: string; thinking?: string; signature?: string }>)
      .find((b) => b.type === "thinking");
    expect(thinkingBlock).toBeDefined();
    expect(thinkingBlock?.thinking).toBe("reasoning step 1");
    expect(thinkingBlock?.signature).toBe("sig-abc-123");
  });

  it("max_tokens recovery keeps thinking block, drops tool_use", async () => {
    const { turn, llm, auditEvents } = await makeFixture([
      {
        blocks: [
          { type: "thinking", thinking: "partial reasoning", signature: "sig-xyz" },
          { type: "text", text: "I'll call " },
          { type: "tool_use", id: "tu_partial", name: "Bash", input: {} },
        ],
        stopReason: "max_tokens",
      },
      { blocks: [{ type: "text", text: "done." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    const secondCall = llm.calls[1]!;
    const priorAssistant = secondCall.messages.find((m) => m.role === "assistant");
    const content = priorAssistant?.content as Array<{ type: string }> | undefined;
    expect(content?.some((b) => b.type === "thinking")).toBe(true);
    expect(content?.some((b) => b.type === "tool_use")).toBe(false);
    expect(
      auditEvents.find((e) => e.event === "output_recovery_drop_unresolved_tool_use"),
    ).toBeDefined();
  });

  it("max_tokens with only tool_use (no text) → empty assistant msg omitted", async () => {
    const { turn, llm } = await makeFixture([
      {
        blocks: [
          { type: "tool_use", id: "tu_only", name: "Bash", input: {} },
        ],
        stopReason: "max_tokens",
      },
      { blocks: [{ type: "text", text: "recovered." }], stopReason: "end_turn" },
    ]);
    await turn.execute();
    expect(llm.calls.length).toBe(2);
    // After filtering, filteredBlocks is empty, so no assistant message
    // is pushed — the recovery call sees the prior context + only a
    // Continue. user message.
    const secondCall = llm.calls[1]!;
    const lastMsg = secondCall.messages[secondCall.messages.length - 1];
    expect(lastMsg?.role).toBe("user");
    expect(lastMsg?.content).toBe("Continue.");
  });
});
