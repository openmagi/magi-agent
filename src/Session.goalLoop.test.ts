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
    previousGoalLoop = process.env.CORE_AGENT_GOAL_LOOP;
    process.env.CORE_AGENT_GOAL_LOOP = "1";
  });

  afterEach(async () => {
    if (previousGoalLoop === undefined) delete process.env.CORE_AGENT_GOAL_LOOP;
    else process.env.CORE_AGENT_GOAL_LOOP = previousGoalLoop;
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
});
