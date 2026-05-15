import { describe, it, expect, beforeEach } from "vitest";
import { useChatStore } from "./chat-store";
import { INTERRUPTED_SUFFIX, MAX_QUEUED_MESSAGES } from "./queue-constants";
import type {
  ChatMessage,
  ControlRequestRecord,
  MissionActivity,
  QueuedMessage,
  SubagentActivity,
} from "./types";

function makeQueued(content: string, replyTo?: QueuedMessage["replyTo"]): QueuedMessage {
  return {
    id: `q-${content}-${Math.random().toString(36).slice(2, 8)}`,
    content,
    queuedAt: Date.now(),
    ...(replyTo ? { replyTo } : {}),
  };
}

const blockedMission: MissionActivity = {
  id: "mission-blocked",
  title: "Draft weekly research report",
  kind: "goal",
  status: "blocked",
  detail: "Waiting for approval",
  updatedAt: 123,
};

const completedMission: MissionActivity = {
  id: "mission-done",
  title: "Archive old run",
  kind: "manual",
  status: "completed",
  updatedAt: 122,
};

describe("chat-store message queue", () => {
  beforeEach(() => {
    // Reset queuedMessages between tests — zustand state persists across tests.
    if (typeof localStorage !== "undefined") localStorage.clear();
    useChatStore.setState({
      botId: null,
      channels: [],
      activeChannel: "general",
      messages: {},
      channelStates: {},
      serverMessages: {},
      lastServerFetch: {},
      abortControllers: {},
      queuedMessages: {},
      deletedIds: {},
      selectionMode: false,
      selectedMessages: {},
      controlRequests: {},
    });
  });

  it("enqueueMessage returns true under the cap", () => {
    const ok = useChatStore.getState().enqueueMessage("general", makeQueued("hi"));
    expect(ok).toBe(true);
    expect(useChatStore.getState().queuedMessages.general).toHaveLength(1);
  });

  it("enqueueMessage rejects once MAX_QUEUED_MESSAGES reached", () => {
    const st = useChatStore.getState();
    for (let i = 0; i < MAX_QUEUED_MESSAGES; i++) {
      expect(st.enqueueMessage("general", makeQueued(`m${i}`))).toBe(true);
    }
    expect(st.enqueueMessage("general", makeQueued("overflow"))).toBe(false);
    expect(useChatStore.getState().queuedMessages.general).toHaveLength(MAX_QUEUED_MESSAGES);
  });

  it("dequeueFirst returns FIFO and shrinks the queue", () => {
    const st = useChatStore.getState();
    st.enqueueMessage("general", makeQueued("first"));
    st.enqueueMessage("general", makeQueued("second"));
    const next = useChatStore.getState().dequeueFirst("general");
    expect(next?.content).toBe("first");
    expect(useChatStore.getState().queuedMessages.general).toHaveLength(1);
  });

  it("dequeueFirst drains now-priority messages before older normal messages", () => {
    const st = useChatStore.getState();
    st.enqueueMessage("general", { ...makeQueued("normal-first"), priority: "next" });
    st.enqueueMessage("general", { ...makeQueued("forced-now"), priority: "now" });
    st.enqueueMessage("general", { ...makeQueued("normal-second"), priority: "next" });

    expect(useChatStore.getState().dequeueFirst("general")?.content).toBe("forced-now");
    expect(useChatStore.getState().dequeueFirst("general")?.content).toBe("normal-first");
    expect(useChatStore.getState().dequeueFirst("general")?.content).toBe("normal-second");
  });

  it("promoteNextQueuedMessage marks the oldest queued message as now priority", () => {
    const st = useChatStore.getState();
    const first = makeQueued("first");
    const second = makeQueued("second");
    st.enqueueMessage("general", first);
    st.enqueueMessage("general", second);

    expect(useChatStore.getState().promoteNextQueuedMessage("general")).toBe(true);
    expect(useChatStore.getState().queuedMessages.general?.[0]).toMatchObject({
      id: first.id,
      priority: "now",
    });
  });

  it("dequeueFirst returns null on empty queue", () => {
    expect(useChatStore.getState().dequeueFirst("general")).toBeNull();
  });

  it("preserves replyTo through the queue", () => {
    const replyTo = { messageId: "abc", preview: "what about X?", role: "assistant" as const };
    useChatStore.getState().enqueueMessage("general", makeQueued("follow-up", replyTo));
    const drained = useChatStore.getState().dequeueFirst("general");
    expect(drained?.replyTo).toEqual(replyTo);
  });

  it("removeFromQueue removes a specific id", () => {
    const a = makeQueued("a");
    const b = makeQueued("b");
    useChatStore.getState().enqueueMessage("general", a);
    useChatStore.getState().enqueueMessage("general", b);
    useChatStore.getState().removeFromQueue("general", a.id);
    const rest = useChatStore.getState().queuedMessages.general;
    expect(rest).toHaveLength(1);
    expect(rest[0].id).toBe(b.id);
  });

  it("clearQueue wipes the channel queue", () => {
    useChatStore.getState().enqueueMessage("general", makeQueued("a"));
    useChatStore.getState().clearQueue("general");
    expect(useChatStore.getState().queuedMessages.general).toBeUndefined();
  });

  it("starts message selection without preselecting a message so header export can use select all", () => {
    useChatStore.setState({
      messages: {
        general: [
          { id: "local-user", role: "user", content: "First", timestamp: 1 } satisfies ChatMessage,
        ],
      },
      serverMessages: {
        general: [
          { id: "server-assistant", role: "assistant", content: "Second", timestamp: 2 } satisfies ChatMessage,
        ],
      },
    });

    useChatStore.getState().startSelectionMode("general");

    expect(useChatStore.getState().selectionMode).toBe(true);
    expect(useChatStore.getState().selectedMessages.general?.size ?? 0).toBe(0);

    useChatStore.getState().selectAllMessages("general");
    expect(useChatStore.getState().selectedMessages.general).toEqual(
      new Set(["local-user", "server-assistant"]),
    );
  });

  it("cancelStream annotates partial assistant text with interrupted suffix", () => {
    // Simulate an in-flight stream with partial text.
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "partial answer",
          thinkingText: "",
          error: null,
          thinkingStartedAt: Date.now(),
          activeTools: [],
          taskBoard: null,
        },
      },
      activeChannel: "general",
      messages: { general: [] },
    });
    useChatStore.getState().cancelStream("general");
    const msgs = useChatStore.getState().messages.general;
    const last = msgs[msgs.length - 1];
    expect(last.role).toBe("assistant");
    expect(last.content.endsWith(INTERRUPTED_SUFFIX)).toBe(true);
  });

  it("cancelStream clears the queue for that channel", () => {
    useChatStore.getState().enqueueMessage("general", makeQueued("doomed"));
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true, streamingText: "", thinkingText: "", error: null,
          thinkingStartedAt: null, activeTools: [], taskBoard: null,
        },
      },
    });
    useChatStore.getState().cancelStream("general");
    expect(useChatStore.getState().queuedMessages.general).toBeUndefined();
  });

  it("cancelStream preserves the queue when requested for ESC handoff", () => {
    useChatStore.getState().enqueueMessage("general", makeQueued("send next"));
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true, streamingText: "", thinkingText: "", error: null,
          hasTextContent: false,
          thinkingStartedAt: null, activeTools: [], taskBoard: null,
          turnPhase: "executing", heartbeatElapsedMs: null, pendingInjectionCount: 0,
        },
      },
    });
    useChatStore.getState().cancelStream("general", { preserveQueue: true });
    expect(useChatStore.getState().queuedMessages.general?.[0]?.content).toBe("send next");
  });

  it("finalizeStream explains thinking-only turns with no visible answer text", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "",
          thinkingText: "internal reasoning",
          error: null,
          hasTextContent: false,
          thinkingStartedAt: Date.now(),
          activeTools: [],
          taskBoard: null,
          turnPhase: "executing",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
          responseLanguage: "ko",
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general");

    const messages = useChatStore.getState().messages.general ?? [];
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      role: "assistant",
      content: "⚠️ 작업은 진행됐지만 최종 답변 텍스트가 도착하지 않았습니다. 다시 시도해 주세요.",
      thinkingContent: "internal reasoning",
    });
  });

  it("finalizeStream attaches final turn usage to assistant messages", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "final answer",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: null,
          activeTools: [],
          taskBoard: null,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
          turnUsage: {
            inputTokens: 1234,
            outputTokens: 56,
            costUsd: 0.0123,
          },
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general", "assistant-usage");

    expect(useChatStore.getState().messages.general?.[0]).toMatchObject({
      id: "assistant-usage",
      role: "assistant",
      content: "final answer",
      usage: {
        inputTokens: 1234,
        outputTokens: 56,
        costUsd: 0.0123,
      },
    });
  });

  it("finalizeStream marks partial aborted text as incomplete", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText:
            "먼저 Bull/Bear 파트너 두 명을 병렬로 띄우겠습니다.",
          thinkingText: "",
          error: "gateway timeout",
          hasTextContent: true,
          thinkingStartedAt: null,
          activeTools: [],
          taskBoard: null,
          turnPhase: "aborted",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
          responseLanguage: "ko",
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general");

    const message = useChatStore.getState().messages.general?.[0];
    expect(message?.content).toContain("먼저 Bull/Bear 파트너");
    expect(message?.content).toContain("응답 생성이 중단되었습니다");
    expect(message?.content).toContain("gateway timeout");
  });

  it("finalizeStream keeps visible text unchanged for source-verification errors", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText:
            "브라우저 기본 기능 테스트 완료했습니다. 세션 생성과 스냅샷이 정상 동작합니다.",
          thinkingText: "",
          error:
            "I could not complete a source-verified final answer for this request. Please retry with a narrower scope or ask me to continue from the inspected-source context.",
          hasTextContent: true,
          thinkingStartedAt: null,
          activeTools: [],
          taskBoard: null,
          turnPhase: "aborted",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
          responseLanguage: "ko",
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general");

    const message = useChatStore.getState().messages.general?.[0];
    expect(message?.content).toBe(
      "브라우저 기본 기능 테스트 완료했습니다. 세션 생성과 스냅샷이 정상 동작합니다.",
    );
    expect(message?.content).not.toContain("검증 경고");
    expect(message?.content).not.toContain("응답 생성이 중단되었습니다");
  });

  it("finalizeStream keeps visible text unchanged for runtime-verifier errors", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText:
            "브라우저 기본 기능 테스트 완료했습니다. 세션 생성과 스냅샷이 정상 동작합니다.",
          thinkingText: "",
          error:
            "The runtime verifier stopped this run because the assistant promised work without completing it.",
          hasTextContent: true,
          thinkingStartedAt: null,
          activeTools: [],
          taskBoard: null,
          turnPhase: "aborted",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
          responseLanguage: "ko",
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general");

    const message = useChatStore.getState().messages.general?.[0];
    expect(message?.content).toBe(
      "브라우저 기본 기능 테스트 완료했습니다. 세션 생성과 스냅샷이 정상 동작합니다.",
    );
    expect(message?.content).not.toContain("runtime verifier");
    expect(message?.content).not.toContain("응답 생성이 중단되었습니다");
  });

  it("addMessage is idempotent when overlapping history loads deliver the same row", () => {
    const first: ChatMessage = {
      id: "client-1",
      serverId: "server-1",
      role: "assistant",
      content: "first copy",
      timestamp: 200,
    };
    const duplicateWithFreshContent: ChatMessage = {
      id: "client-1",
      serverId: "server-1",
      role: "assistant",
      content: "fresh copy",
      timestamp: 200,
    };

    useChatStore.getState().addMessage("general", {
      id: "client-2",
      role: "user",
      content: "later",
      timestamp: 300,
    });
    useChatStore.getState().addMessage("general", first);
    useChatStore.getState().addMessage("general", duplicateWithFreshContent);

    expect(useChatStore.getState().messages.general.map((m) => m.id)).toEqual([
      "client-1",
      "client-2",
    ]);
    expect(useChatStore.getState().messages.general[0].content).toBe("fresh copy");
  });

  it("addMessage keeps distinct local messages without server ids", () => {
    useChatStore.getState().addMessage("general", {
      id: "local-1",
      role: "user",
      content: "first local",
      timestamp: 100,
    });
    useChatStore.getState().addMessage("general", {
      id: "local-2",
      role: "assistant",
      content: "second local",
      timestamp: 100,
    });

    expect(useChatStore.getState().messages.general.map((m) => m.content)).toEqual([
      "first local",
      "second local",
    ]);
  });

  it("ignores stale bot-scoped message writes after switching bots", () => {
    useChatStore.getState().setBotId("bot-a");
    useChatStore.getState().setBotId("bot-b");

    useChatStore.getState().addMessage("general", {
      id: "stale-user",
      role: "user",
      content: "from bot A",
      timestamp: 100,
    }, { botId: "bot-a" });

    expect(useChatStore.getState().messages.general).toBeUndefined();
  });

  it("aborts in-flight stream controllers when switching bots", () => {
    useChatStore.getState().setBotId("bot-a");
    const controller = new AbortController();
    useChatStore.getState().setAbortController("general", controller);

    useChatStore.getState().setBotId("bot-b");

    expect(controller.signal.aborted).toBe(true);
    expect(useChatStore.getState().abortControllers).toEqual({});
  });

  it("hydrates and upserts pending control requests outside ChannelState", () => {
    const req = makeControlRequest("cr_1");
    useChatStore.getState().hydrateControlRequests("general", [req]);
    expect(useChatStore.getState().controlRequests.general).toEqual([req]);

    const updated = { ...req, state: "approved" as const, decision: "approved" as const };
    useChatStore.getState().upsertControlRequest("general", updated);
    expect(useChatStore.getState().controlRequests.general).toEqual([updated]);
  });

  it("applies control request lifecycle events without being cleared by stream finalization", () => {
    const req = makeControlRequest("cr_1");
    useChatStore.getState().applyControlEvent("general", {
      type: "control_request_created",
      request: req,
    });
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "answer",
          thinkingText: "",
          error: null,
          hasTextContent: true,
        },
      },
      messages: { general: [] },
    });
    useChatStore.getState().finalizeStream("general");
    expect(useChatStore.getState().controlRequests.general?.[0]?.requestId).toBe("cr_1");

    useChatStore.getState().applyControlEvent("general", {
      type: "control_request_resolved",
      requestId: "cr_1",
      decision: "denied",
      feedback: "no",
    });
    expect(useChatStore.getState().controlRequests.general?.[0]).toMatchObject({
      state: "denied",
      decision: "denied",
      feedback: "no",
    });
  });

  it("clears live run metadata when terminal updates mark a channel idle", () => {
    const runningSubagent: SubagentActivity = {
      taskId: "task-running",
      role: "writer",
      status: "running",
      detail: "Drafting chapter 4",
      startedAt: 123,
      updatedAt: 456,
    };
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "final answer",
          thinkingText: "thinking",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: true,
          activeTools: [
            { id: "tool-1", label: "FileRead", status: "running", startedAt: 123 },
          ],
          subagents: [runningSubagent],
          taskBoard: {
            receivedAt: 123,
            tasks: [
              { id: "task-1", title: "Read files", description: "", status: "in_progress" },
            ],
          },
          fileProcessing: true,
          turnPhase: "executing",
          heartbeatElapsedMs: 5000,
          pendingInjectionCount: 1,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streaming: false,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      reconnecting: false,
      turnPhase: null,
      heartbeatElapsedMs: null,
      pendingInjectionCount: 0,
    });

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      thinkingStartedAt: null,
      reconnecting: false,
      activeTools: [],
      subagents: [runningSubagent],
      taskBoard: null,
      fileProcessing: false,
      turnPhase: null,
      heartbeatElapsedMs: null,
      pendingInjectionCount: 0,
    });
  });

  it("keeps runtime traces visible after a terminal verifier error", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "",
          thinkingText: "",
          error: null,
          hasTextContent: false,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: [],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "executing",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
          runtimeTraces: [
            {
              turnId: "turn-1",
              phase: "terminal_abort",
              severity: "error",
              title: "Turn aborted before completion",
              detail: "Verifier retry limit reached.",
              receivedAt: 123,
            },
          ],
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streaming: false,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      error: "Verifier retry limit reached.",
      turnPhase: "aborted",
    });

    expect(useChatStore.getState().channelStates.general.runtimeTraces).toEqual([
      expect.objectContaining({
        phase: "terminal_abort",
        title: "Turn aborted before completion",
      }),
    ]);
  });

  it("keeps running background subagents visible when finalizing a parent stream", () => {
    const runningSubagent: SubagentActivity = {
      taskId: "task-running",
      role: "writer",
      status: "running",
      detail: "Drafting chapter 4",
      startedAt: 123,
      updatedAt: 456,
    };
    const completedSubagent: SubagentActivity = {
      taskId: "task-done",
      role: "reviewer",
      status: "done",
      startedAt: 100,
      updatedAt: 200,
    };
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "I will keep watching this.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: [runningSubagent, completedSubagent],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general", "assistant-final");

    expect(useChatStore.getState().messages.general?.[0]).toMatchObject({
      id: "assistant-final",
      role: "assistant",
      content: "I will keep watching this.",
    });
    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      subagents: [runningSubagent],
      turnPhase: null,
    });
  });

  it("keeps non-terminal durable missions visible when finalizing a parent stream", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "I will wait for approval.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: [],
          taskBoard: null,
          missions: [blockedMission, completedMission],
          activeGoalMissionId: blockedMission.id,
          fileProcessing: false,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general", "assistant-final");

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      missions: [blockedMission],
      activeGoalMissionId: blockedMission.id,
      turnPhase: null,
    });
  });

  it("captures research evidence on finalized assistant messages", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "Here is the cited answer.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: [],
          taskBoard: null,
          inspectedSources: [{
            sourceId: "src_child_1",
            kind: "subagent_result",
            uri: "child-agent://bull-case",
            title: "Bull case partner",
            inspectedAt: 456,
          }],
          citationGate: {
            ruleId: "claim-citation-gate",
            verdict: "ok",
            checkedAt: 789,
          },
          fileProcessing: false,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general", "assistant-final");

    expect(useChatStore.getState().messages.general?.[0]).toMatchObject({
      id: "assistant-final",
      role: "assistant",
      content: "Here is the cited answer.",
      researchEvidence: {
        inspectedSources: [{
          sourceId: "src_child_1",
          kind: "subagent_result",
          uri: "child-agent://bull-case",
          title: "Bull case partner",
          inspectedAt: 456,
        }],
        citationGate: {
          ruleId: "claim-citation-gate",
          verdict: "ok",
          checkedAt: 789,
        },
      },
    });
    expect(useChatStore.getState().channelStates.general).toMatchObject({
      inspectedSources: [],
      citationGate: null,
    });
  });

  it("clears stale live metadata when starting a fresh run from idle state", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: false,
          streamingText: "",
          thinkingText: "",
          error: null,
          hasTextContent: false,
          thinkingStartedAt: null,
          reconnecting: false,
          activeTools: [
            { id: "tool-1", label: "Old tool", status: "running", startedAt: 123 },
          ],
          taskBoard: {
            receivedAt: 123,
            tasks: [
              { id: "task-1", title: "Old task", description: "", status: "in_progress" },
            ],
          },
          missions: [blockedMission],
          activeGoalMissionId: blockedMission.id,
          fileProcessing: false,
          turnPhase: null,
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      error: null,
      thinkingStartedAt: 456,
      fileProcessing: false,
      turnPhase: "pending",
      heartbeatElapsedMs: null,
      pendingInjectionCount: 0,
    });

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: true,
      activeTools: [],
      taskBoard: null,
      missions: [blockedMission],
      activeGoalMissionId: blockedMission.id,
      turnPhase: "pending",
    });
  });

  it("clears stale research telemetry when starting a fresh run", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: false,
          streamingText: "",
          thinkingText: "",
          error: null,
          inspectedSources: [{
            sourceId: "src_old",
            kind: "web_fetch",
            uri: "https://old.example",
            inspectedAt: 1,
          }],
          citationGate: {
            ruleId: "claim-citation-gate",
            verdict: "violation",
            detail: "1 uncited claim",
            checkedAt: 1,
          },
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      error: null,
      thinkingStartedAt: 456,
    });

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: true,
      inspectedSources: [],
      citationGate: null,
    });
  });

  it("clears transient connection retry banners when a retried stream produces live content", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "",
          thinkingText: "",
          error: "Connecting to bot... (2/8)",
          hasTextContent: false,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "pending",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streamingText: "Recovered response",
      hasTextContent: true,
    });

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      error: null,
      streamingText: "Recovered response",
      hasTextContent: true,
    });
  });

  it("clears reconnecting once live work progress arrives", () => {
    const runningSubagent: SubagentActivity = {
      taskId: "task-running",
      role: "writer",
      status: "running",
      detail: "Drafting chapter 4",
      startedAt: 123,
      updatedAt: 456,
    };
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "partial answer",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: true,
          activeTools: [],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "executing",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      subagents: [runningSubagent],
    });

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      reconnecting: false,
      subagents: [runningSubagent],
    });
  });

  it("clears reconnecting when research telemetry arrives", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "",
          thinkingText: "",
          error: null,
          reconnecting: true,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      inspectedSources: [{
        sourceId: "src_1",
        kind: "web_fetch",
        uri: "https://example.com/report",
        inspectedAt: 123,
      }],
    });

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      reconnecting: false,
    });
  });

  it("keeps non-connection errors until they are explicitly replaced", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "",
          thinkingText: "",
          error: `Queue full (max ${MAX_QUEUED_MESSAGES}). Wait for the bot to finish.`,
          hasTextContent: false,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "pending",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streamingText: "Still running",
      hasTextContent: true,
    });

    expect(useChatStore.getState().channelStates.general.error).toBe(
      `Queue full (max ${MAX_QUEUED_MESSAGES}). Wait for the bot to finish.`,
    );
  });

  it("finalizeStream preserves queued messages so drain can fire after turn commit", () => {
    useChatStore.getState().enqueueMessage("general", makeQueued("follow-up"));
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "done answer",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: null,
          activeTools: [],
          taskBoard: null,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general");

    expect(useChatStore.getState().messages.general).toHaveLength(1);
    expect(useChatStore.getState().messages.general?.[0]?.content).toBe("done answer");
    const queued = useChatStore.getState().queuedMessages.general;
    expect(queued).toHaveLength(1);
    expect(queued?.[0]?.content).toBe("follow-up");

    const drained = useChatStore.getState().dequeueFirst("general");
    expect(drained?.content).toBe("follow-up");
    expect(useChatStore.getState().queuedMessages.general).toHaveLength(0);
  });

  it("double finalizeStream with different IDs does not create duplicate messages", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "answer text",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: null,
          activeTools: [],
          taskBoard: null,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().finalizeStream("general");
    expect(useChatStore.getState().messages.general).toHaveLength(1);

    // Second finalizeStream after state was reset should NOT add another message
    // because finalizeStream resets channelState (streamingText becomes empty)
    useChatStore.getState().finalizeStream("general", "assistant-duplicate");
    expect(useChatStore.getState().messages.general).toHaveLength(1);
  });
});

function makeControlRequest(requestId: string): ControlRequestRecord {
  return {
    requestId,
    kind: "tool_permission",
    state: "pending",
    sessionKey: "agent:main:app:general",
    channelName: "general",
    source: "turn",
    prompt: "Allow Bash?",
    createdAt: 1,
    expiresAt: Date.now() + 60_000,
  };
}
