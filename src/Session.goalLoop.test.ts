import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ServerResponse } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Agent } from "./Agent.js";
import { HookRegistry } from "./hooks/HookRegistry.js";
import { Session, type SessionMeta } from "./Session.js";
import type { LLMEvent, LLMStreamRequest } from "./transport/LLMClient.js";
import { SseWriter } from "./transport/SseWriter.js";
import type { ChannelRef } from "./util/types.js";

class CaptureSse extends SseWriter {
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

  override legacyDelta(): void {}
  override legacyFinish(): void {}
  override start(): void {}
  override end(): void {}
}

class ScriptedGoalLlm {
  readonly calls: LLMStreamRequest[] = [];
  protected readonly normalReplies = ["First pass", "Second pass"];
  protected readonly specReplies = [
    '{"title":"Ship launch memo","objective":"Ship the launch memo","completionCriteria":["Final memo delivered"]}',
  ];
  protected readonly judgeReplies = [
    '{"decision":"continue","reason":"Need another pass"}',
    '{"decision":"done","reason":"Complete"}',
  ];

  async *stream(req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    this.calls.push(req);
    const isJudge = String(req.system ?? "").includes("goal mission judge");
    const isSpec = String(req.system ?? "").includes("goal mission distiller");
    const delta = isSpec
      ? this.specReplies.shift()
      : isJudge
      ? this.judgeReplies.shift()
      : this.normalReplies.shift();
    if (!delta) throw new Error("ScriptedGoalLlm exhausted");
    yield { kind: "text_delta", blockIndex: 0, delta };
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    };
  }
}

async function eventually(assertion: () => void): Promise<void> {
  const started = Date.now();
  let lastError: unknown;
  while (Date.now() - started < 1000) {
    try {
      assertion();
      return;
    } catch (err) {
      lastError = err;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
  throw lastError;
}

describe("Session goal loop automation", () => {
  let workspaceRoot: string;
  let previousGoalLoop: string | undefined;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "session-goal-loop-"));
    previousGoalLoop = process.env.MAGI_GOAL_LOOP;
    process.env.MAGI_GOAL_LOOP = "1";
  });

  afterEach(async () => {
    if (previousGoalLoop === undefined) delete process.env.MAGI_GOAL_LOOP;
    else process.env.MAGI_GOAL_LOOP = previousGoalLoop;
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("continues a goal mission in the background until the judge marks it done", async () => {
    const llm = new ScriptedGoalLlm();
    const missionClient = {
      createMission: vi.fn(async () => ({
        id: "mission-1",
        title: "Ship the launch memo",
        kind: "goal",
        status: "running",
      })),
      createRun: vi.fn(async () => ({ id: `run-${missionClient.createRun.mock.calls.length + 1}` })),
      appendEvent: vi.fn(async () => ({})),
    };
    const deliveries: Array<{ channel: ChannelRef; text: string }> = [];
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:1",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    let turnNo = 0;
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => `turn-${++turnNo}`,
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      deliverAssistantTextToChannel: vi.fn(async (channel: ChannelRef, text: string) => {
        deliveries.push({ channel, text });
      }),
    } as unknown as Agent;

    const session = new Session(sessionMeta, agent);
    const result = await session.runTurn(
      { text: "Ship the launch memo", receivedAt: Date.now() },
      new CaptureSse(),
      { goalMode: true },
    );

    expect(result.assistantText).toBe("First pass");
    await eventually(() => {
      expect(deliveries).toHaveLength(1);
    });
    expect(deliveries[0]).toEqual({
      channel: { type: "app", channelId: "general" },
      text: "Second pass",
    });
    expect(missionClient.createMission).toHaveBeenCalledTimes(1);
    expect(missionClient.createRun.mock.calls.map((call) => call[1].triggerType)).toEqual([
      "user",
      "goal_continue",
    ]);
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        eventType: "heartbeat",
        message: "Goal continuation scheduled",
      }),
    );
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        eventType: "completed",
        message: "Complete",
      }),
    );
    const contract = session.executionContract.snapshot();
    expect(contract.control).toEqual({
      mode: "heavy",
      reason: "goal_loop_continuation",
    });
    expect(contract.taskState.goal).toBe("Ship the launch memo");
    expect(contract.taskState.verificationMode).toBe("sample");
    expect(contract.taskState.verificationEvidence).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: "hook",
          status: "passed",
          detail: expect.stringContaining("Goal judge marked complete"),
          assertions: expect.arrayContaining([
            "decision=done",
            "objective=Ship the launch memo",
          ]),
        }),
      ]),
    );
  });

  it("creates goal missions with a compact title and explicit completion criteria", async () => {
    class LongGoalLlm extends ScriptedGoalLlm {
      protected readonly specReplies = [
        JSON.stringify({
          title: "내외디스틸러리 TIPS 투자심의",
          objective: "내외디스틸러리 1억원 TIPS LP 투자 여부를 검토한다.",
          completionCriteria: [
            "시장 전망과 회사 리스크 검토",
            "재무제표 기반 투자 판단",
            "최종 IC 보고서 작성",
          ],
        }),
      ];
      protected readonly judgeReplies = ['{"decision":"done","reason":"Mission title is distilled"}'];
    }
    const llm = new LongGoalLlm();
    const missionClient = {
      createMission: vi.fn(async (input: {
        title: string;
        kind: string;
        status: string;
        summary?: string;
        metadata?: Record<string, unknown>;
      }) => ({
        id: "mission-1",
        title: input.title,
        kind: input.kind,
        status: input.status,
      })),
      createRun: vi.fn(async () => ({ id: "run-1" })),
      appendEvent: vi.fn(async () => ({})),
    };
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:1",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => "turn-1",
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      deliverAssistantTextToChannel: vi.fn(async () => undefined),
    } as unknown as Agent;
    const raw = [
      "이 자료들을 기반으로 내외디스틸러리에 대한 TIPS LP 투자(1억원) 건에 대해 투심위를 열어줘.",
      "Opus 4.6 서브에이전트로 낙관적 파트너와 회의적 파트너 의견을 받고 GPT5.5 리뷰까지 진행해.",
      "최종 IC 보고서까지 작성해줘.",
    ].join("\n");

    const session = new Session(sessionMeta, agent);
    await session.runTurn(
      { text: raw, receivedAt: Date.now() },
      new CaptureSse(),
      { goalMode: true },
    );

    expect(missionClient.createMission).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "내외디스틸러리 TIPS 투자심의",
        summary: "내외디스틸러리 1억원 TIPS LP 투자 여부를 검토한다.",
        metadata: expect.objectContaining({
          objective: "내외디스틸러리 1억원 TIPS LP 투자 여부를 검토한다.",
          sourceRequest: raw,
          completionCriteria: [
            "시장 전망과 회사 리스크 검토",
            "재무제표 기반 투자 판단",
            "최종 IC 보고서 작성",
          ],
        }),
      }),
    );
    const created = missionClient.createMission.mock.calls[0]?.[0];
    expect(`${created?.title} ${created?.summary} ${created?.metadata?.objective}`).not.toContain(
      "Opus 4.6 서브에이전트",
    );
  });

  it("resumes a restart-recovered goal mission with a resume run", async () => {
    class ResumeGoalLlm extends ScriptedGoalLlm {
      protected readonly normalReplies = ["Recovered pass"];
      protected readonly judgeReplies = ['{"decision":"done","reason":"Recovered"}'];
    }
    const llm = new ResumeGoalLlm();
    const missionClient = {
      createMission: vi.fn(),
      createRun: vi.fn(async () => ({ id: "run-resume-1" })),
      appendEvent: vi.fn(async () => ({})),
    };
    const deliveries: Array<{ channel: ChannelRef; text: string; source: string }> = [];
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:32",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => "turn-resume-1",
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      deliverAssistantTextToChannel: vi.fn(async (channel: ChannelRef, text: string, source: string) => {
        deliveries.push({ channel, text, source });
      }),
    } as unknown as Agent;

    const session = new Session(sessionMeta, agent);
    await session.resumeGoalAfterRestart({
      actionEventId: "event-retry-1",
      missionId: "mission-1",
      startedAt: "2026-05-09T15:15:14.000Z",
      objective: "Finish the IC memo",
      sourceRequest: "Run the investment committee workflow",
      title: "Investment memo",
      completionCriteria: ["Final IC memo delivered"],
      turnsUsed: 2,
      maxTurns: 30,
      resumeContext:
        "Recent mission ledger before restart:\n" +
        "- heartbeat: Drafted market sizing and queued the partner critique.",
    });

    expect(deliveries).toEqual([
      {
        channel: { type: "app", channelId: "general" },
        text: "Recovered pass",
        source: "goal",
      },
    ]);
    expect(missionClient.createRun).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        triggerType: "resume",
        status: "running",
        sessionKey: "agent:main:app:general:32",
        turnId: "turn-resume-1",
        metadata: expect.objectContaining({
          objective: "Finish the IC memo",
          turnsUsed: 2,
          restartRecovery: true,
          actionEventId: "event-retry-1",
        }),
      }),
    );
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        eventType: "resumed",
        message: "Goal mission resumed after restart",
      }),
    );
    const normalCall = llm.calls.find(
      (call) =>
        !String(call.system ?? "").includes("goal mission judge") &&
        !String(call.system ?? "").includes("goal mission distiller"),
    );
    expect(JSON.stringify(normalCall?.messages)).toContain(
      "Drafted market sizing and queued the partner critique.",
    );
    await eventually(() => {
      expect(missionClient.appendEvent).toHaveBeenCalledWith(
        "mission-1",
        expect.objectContaining({
          eventType: "completed",
          message: "Recovered",
        }),
      );
    });
  });

  it("resumes a user-retried goal mission with a retry run and action ledger reason", async () => {
    class RetryGoalLlm extends ScriptedGoalLlm {
      protected readonly normalReplies = ["Manual retry pass"];
      protected readonly judgeReplies = ['{"decision":"done","reason":"Retried and complete"}'];
    }
    const llm = new RetryGoalLlm();
    const missionClient = {
      createMission: vi.fn(),
      createRun: vi.fn(async () => ({ id: "run-retry-1" })),
      appendEvent: vi.fn(async () => ({})),
    };
    const deliveries: Array<{ channel: ChannelRef; text: string; source: string }> = [];
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:32",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => "turn-retry-1",
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      deliverAssistantTextToChannel: vi.fn(async (channel: ChannelRef, text: string, source: string) => {
        deliveries.push({ channel, text, source });
      }),
    } as unknown as Agent;

    const session = new Session(sessionMeta, agent);
    await session.resumeGoalAfterRestart({
      actionEventId: "event-retry-1",
      missionId: "mission-1",
      sourceEventType: "retry_requested",
      reason: "manual_retry",
      objective: "Finish the IC memo",
      sourceRequest: "Run the investment committee workflow",
      title: "Investment memo",
      completionCriteria: ["Final IC memo delivered"],
      turnsUsed: 4,
      maxTurns: 30,
      resumeContext: "User requested retry: retry after adding the deck",
    } as Parameters<typeof session.resumeGoalAfterRestart>[0] & {
      sourceEventType: "retry_requested";
      reason: "manual_retry";
    });

    expect(deliveries).toEqual([
      {
        channel: { type: "app", channelId: "general" },
        text: "Manual retry pass",
        source: "goal",
      },
    ]);
    expect(missionClient.createRun).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        triggerType: "retry",
        status: "running",
        sessionKey: "agent:main:app:general:32",
        turnId: "turn-retry-1",
        metadata: expect.objectContaining({
          objective: "Finish the IC memo",
          turnsUsed: 4,
          actionEventId: "event-retry-1",
          sourceEventType: "retry_requested",
        }),
      }),
    );
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        eventType: "resumed",
        message: "Goal mission retry requested by user",
        payload: expect.objectContaining({
          actionEventId: "event-retry-1",
          reason: "manual_retry",
          sourceEventType: "retry_requested",
        }),
      }),
    );
  });

  it("resumes a user-unblocked goal mission with a resume run and action ledger reason", async () => {
    class UnblockGoalLlm extends ScriptedGoalLlm {
      protected readonly normalReplies = ["Manual unblock pass"];
      protected readonly judgeReplies = ['{"decision":"done","reason":"Unblocked and complete"}'];
    }
    const llm = new UnblockGoalLlm();
    const missionClient = {
      createMission: vi.fn(),
      createRun: vi.fn(async () => ({ id: "run-unblock-1" })),
      appendEvent: vi.fn(async () => ({})),
    };
    const deliveries: Array<{ channel: ChannelRef; text: string; source: string }> = [];
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:32",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => "turn-unblock-1",
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      deliverAssistantTextToChannel: vi.fn(async (channel: ChannelRef, text: string, source: string) => {
        deliveries.push({ channel, text, source });
      }),
    } as unknown as Agent;

    const session = new Session(sessionMeta, agent);
    await session.resumeGoalAfterRestart({
      actionEventId: "event-unblock-1",
      missionId: "mission-1",
      sourceEventType: "unblocked",
      reason: "user_unblocked",
      objective: "Finish the IC memo",
      sourceRequest: "Run the investment committee workflow",
      title: "Investment memo",
      completionCriteria: ["Final IC memo delivered"],
      turnsUsed: 4,
      maxTurns: 30,
      resumeContext: "User unblocked mission: token restored",
    });

    expect(deliveries).toEqual([
      {
        channel: { type: "app", channelId: "general" },
        text: "Manual unblock pass",
        source: "goal",
      },
    ]);
    expect(missionClient.createRun).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        triggerType: "resume",
        status: "running",
        sessionKey: "agent:main:app:general:32",
        turnId: "turn-unblock-1",
        metadata: expect.objectContaining({
          objective: "Finish the IC memo",
          turnsUsed: 4,
          actionEventId: "event-unblock-1",
          sourceEventType: "unblocked",
        }),
      }),
    );
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-1",
      expect.objectContaining({
        eventType: "resumed",
        message: "Goal mission resumed by user",
        payload: expect.objectContaining({
          actionEventId: "event-unblock-1",
          reason: "user_unblocked",
          sourceEventType: "unblocked",
        }),
      }),
    );
  });

  it("does not continue a goal mission after it has been cancelled", async () => {
    class ContinueGoalLlm extends ScriptedGoalLlm {
      protected readonly normalReplies = ["First pass"];
      protected readonly judgeReplies = ['{"decision":"continue","reason":"Need more work"}'];
    }
    const llm = new ContinueGoalLlm();
    const missionClient = {
      createMission: vi.fn(async () => ({
        id: "mission-cancelled",
        title: "Ship the launch memo",
        kind: "goal",
        status: "running",
      })),
      createRun: vi.fn(async () => ({ id: "run-cancelled-1" })),
      appendEvent: vi.fn(async () => ({})),
    };
    const deliveries: Array<{ channel: ChannelRef; text: string }> = [];
    const sessionMeta: SessionMeta = {
      sessionKey: "agent:main:app:general:33",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    };
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => `turn-${missionClient.createRun.mock.calls.length + 1}`,
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      isGoalMissionCancelled: vi.fn((missionId: string) => missionId === "mission-cancelled"),
      deliverAssistantTextToChannel: vi.fn(async (channel: ChannelRef, text: string) => {
        deliveries.push({ channel, text });
      }),
    } as unknown as Agent;

    const session = new Session(sessionMeta, agent);
    await session.runTurn(
      { text: "Ship the launch memo", receivedAt: Date.now() },
      new CaptureSse(),
      { goalMode: true },
    );

    await new Promise((resolve) => setTimeout(resolve, 25));

    expect(deliveries).toEqual([]);
    expect(missionClient.createRun).toHaveBeenCalledTimes(1);
    expect(missionClient.appendEvent).toHaveBeenCalledWith(
      "mission-cancelled",
      expect.objectContaining({
        eventType: "cancelled",
        message: "Goal mission cancelled",
      }),
    );
    expect(missionClient.appendEvent).not.toHaveBeenCalledWith(
      "mission-cancelled",
      expect.objectContaining({
        eventType: "heartbeat",
        message: "Goal continuation scheduled",
      }),
    );
  });

  it("does not resume a restart-recovered goal mission after it has been cancelled", async () => {
    const llm = new ScriptedGoalLlm();
    const missionClient = {
      createMission: vi.fn(),
      createRun: vi.fn(async () => ({ id: "run-resume-1" })),
      appendEvent: vi.fn(async () => ({})),
    };
    const agent = {
      config: {
        botId: "bot-goal",
        userId: "user-goal",
        workspaceRoot,
        gatewayToken: "tok",
        apiProxyUrl: "http://api",
        chatProxyUrl: "http://chat",
        redisUrl: "redis://r",
        model: "claude-haiku-4-5",
      },
      hooks: new HookRegistry(),
      tools: { list: () => [], resolve: () => null },
      intent: { classify: async () => ["general"] },
      workspace: { loadIdentity: async () => ({}) },
      auditLog: { append: vi.fn(async () => undefined) },
      llm,
      router: null,
      sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
      contextEngine: {
        assertCompactionFeasible: () => undefined,
        maybeCompact: async () => undefined,
        buildMessagesFromTranscript: () => [],
      },
      missionClient,
      nextTurnId: () => "turn-resume-1",
      registerTurn: vi.fn(),
      unregisterTurn: vi.fn(),
      isGoalMissionCancelled: vi.fn((missionId: string) => missionId === "mission-1"),
      deliverAssistantTextToChannel: vi.fn(async () => undefined),
    } as unknown as Agent;
    const session = new Session({
      sessionKey: "agent:main:app:general:34",
      botId: "bot-goal",
      channel: { type: "app", channelId: "general" },
      createdAt: Date.now(),
      lastActivityAt: Date.now(),
    }, agent);

    await session.resumeGoalAfterRestart({
      actionEventId: "event-retry-1",
      missionId: "mission-1",
      objective: "Finish the IC memo",
      title: "Investment memo",
      completionCriteria: ["Final IC memo delivered"],
      turnsUsed: 2,
      maxTurns: 30,
    });

    expect(missionClient.createRun).not.toHaveBeenCalled();
    expect(missionClient.appendEvent).not.toHaveBeenCalled();
    expect(agent.deliverAssistantTextToChannel).not.toHaveBeenCalled();
  });
});
