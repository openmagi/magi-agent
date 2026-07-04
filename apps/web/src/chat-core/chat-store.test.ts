import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  getResetBoundaryTimestamp,
  getResetCounter,
  syncResetCounters,
  useChatStore,
} from "./chat-store";
import { buildVisibleModelContextMessages } from "./model-context";
import { INTERRUPTED_SUFFIX, MAX_QUEUED_MESSAGES } from "./queue-constants";
import type {
  ChatMessage,
  ControlRequestRecord,
  MissionActivity,
  QueuedMessage,
  SubagentActivity,
  ToolActivity,
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

const backgroundBashTool: ToolActivity = {
  id: "tool-bg-bash",
  label: "Bash",
  status: "done",
  startedAt: 100,
  outputPreview: JSON.stringify({
    backgroundTaskId: "shell_bg_1",
    background: true,
    stdoutFile: "core-agent/logs/shell_bg_1.stdout.log",
    stderrFile: "core-agent/logs/shell_bg_1.stderr.log",
  }),
  durationMs: 42,
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

  it("stores deterministic runtime state and clears it on a fresh run", () => {
    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      error: null,
      determinism: {
        workflowId: "workflow.public",
        workflowVersion: "1.0.0",
        effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
      },
    });

    expect(useChatStore.getState().channelStates.general?.determinism).toMatchObject({
      workflowId: "workflow.public",
    });

    useChatStore.getState().setChannelState("general", {
      streaming: false,
      streamingText: "",
      thinkingText: "",
      error: null,
    });
    expect(useChatStore.getState().channelStates.general?.determinism).toBeUndefined();

    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      error: null,
    });
    expect(useChatStore.getState().channelStates.general?.determinism).toBeUndefined();
  });

  it("clears deterministic runtime state when a streaming retry starts over", () => {
    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      error: null,
      turnPhase: "executing",
      determinism: {
        workflowId: "workflow.public",
        workflowVersion: "1.0.0",
        effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
      },
    });

    useChatStore.getState().setChannelState("general", {
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      turnPhase: "pending",
      activeTools: [],
      subagents: [],
      subagentProgress: {},
      runtimeTraces: [],
    });

    expect(useChatStore.getState().channelStates.general?.determinism).toBeUndefined();
  });

  it("clears deterministic runtime state on pending-to-pending retry reset", () => {
    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      error: null,
      turnPhase: "pending",
      determinism: {
        workflowId: "workflow.public",
        workflowVersion: "1.0.0",
        effectivePolicySnapshotDigest: `sha256:${"1".repeat(64)}`,
      },
    });

    useChatStore.getState().setChannelState("general", {
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      turnPhase: "pending",
      activeTools: [],
      subagents: [],
      subagentProgress: {},
      runtimeTraces: [],
    });

    expect(useChatStore.getState().channelStates.general?.determinism).toBeUndefined();
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

  it("addMessage merges a short exact assistant server copy from the same turn", () => {
    const content = "CRDO 리포트 첨부를 다시 보냈습니다.";
    useChatStore.getState().addMessage("general", {
      id: "user-1",
      role: "user",
      content: "crdo 리포트 첨부 누락됨 다시 보내줘",
      timestamp: 1_000,
    });
    useChatStore.getState().addMessage("general", {
      id: "assistant-local",
      role: "assistant",
      content,
      timestamp: 1_100,
    });

    useChatStore.getState().addMessage("general", {
      id: "assistant-server",
      serverId: "assistant-server",
      role: "assistant",
      content,
      timestamp: 2_200,
    });

    expect(useChatStore.getState().messages.general).toHaveLength(2);
    expect(useChatStore.getState().messages.general?.[1]).toMatchObject({
      id: "assistant-local",
      serverId: "assistant-server",
      content,
    });
  });

  it("addMessage keeps identical short assistant answers across separate user turns", () => {
    const content = "CRDO 리포트 첨부를 다시 보냈습니다.";
    useChatStore.getState().addMessage("general", {
      id: "user-1",
      role: "user",
      content: "crdo 리포트 첨부 누락됨 다시 보내줘",
      timestamp: 1_000,
    });
    useChatStore.getState().addMessage("general", {
      id: "assistant-local",
      role: "assistant",
      content,
      timestamp: 1_100,
    });
    useChatStore.getState().addMessage("general", {
      id: "user-2",
      role: "user",
      content: "한 번 더 보내줘",
      timestamp: 2_000,
    });

    useChatStore.getState().addMessage("general", {
      id: "assistant-server",
      serverId: "assistant-server",
      role: "assistant",
      content,
      timestamp: 2_100,
    });

    expect(useChatStore.getState().messages.general).toHaveLength(4);
    expect(useChatStore.getState().messages.general?.filter((m) => m.content === content)).toHaveLength(2);
  });

  it("removeLocalMessages drops optimistic-only rows without tombstoning server ids", () => {
    useChatStore.getState().addMessage("general", {
      id: "injected-1",
      role: "user",
      content: "Any news?",
      timestamp: 1_800_000_000_000,
      injected: true,
      injectedAfterChars: 0,
    });

    useChatStore.getState().removeLocalMessages("general", new Set(["injected-1"]));

    expect(useChatStore.getState().messages.general ?? []).toHaveLength(0);
    expect(useChatStore.getState().isDeleted("general", "injected-1")).toBe(false);
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

  it("keeps running background and completed subagents visible when finalizing a parent stream", () => {
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
    // Running background subagent stays live; the completed reviewer chip now
    // persists past turn end (T2 retention) instead of vanishing.
    const finalizedSubagents = useChatStore.getState().channelStates.general.subagents ?? [];
    expect(finalizedSubagents.map((subagent) => subagent.taskId).sort()).toEqual(
      ["task-done", "task-running"],
    );
    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      turnPhase: null,
    });
  });

  it("keeps background shell tasks visible when finalizing a parent stream", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "I started this in the background.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [backgroundBashTool],
          subagents: [],
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

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      subagents: [
        {
          taskId: "shell_bg_1",
          role: "bash",
          status: "running",
          detail: "Background command running",
        },
      ],
      turnPhase: null,
    });
  });

  it("retains completed child subagents and their progress after terminal reset, capped at 16", () => {
    // 20 completed (done/error/cancelled) non-background children; only the 16
    // most recent by updatedAt survive.
    const completed: SubagentActivity[] = Array.from({ length: 20 }, (_, index) => ({
      taskId: `child-${index}`,
      role: "researcher",
      status: index % 3 === 0 ? "error" : index % 3 === 1 ? "cancelled" : "done",
      detail: `finished ${index}`,
      startedAt: 100 + index,
      updatedAt: 1000 + index,
    }));
    const subagentProgress: Record<string, { taskId: string; lines: string[] }> = {};
    for (const child of completed) {
      subagentProgress[child.taskId] = { taskId: child.taskId, lines: [`line ${child.taskId}`] };
    }

    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "done with the fan-out.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: completed,
          subagentProgress: subagentProgress as unknown as ChannelState["subagentProgress"],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
    });

    useChatStore.getState().setChannelState("general", {
      streaming: false,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      turnPhase: null,
    });

    const state = useChatStore.getState().channelStates.general;
    const retained = state.subagents ?? [];
    expect(retained).toHaveLength(16);
    // The 16 most-recent-by-updatedAt are child-4 .. child-19.
    const retainedIds = retained.map((subagent) => subagent.taskId).sort();
    expect(retainedIds).not.toContain("child-0");
    expect(retainedIds).not.toContain("child-3");
    expect(retainedIds).toContain("child-19");
    // subagentProgress retained only for the survivors.
    const progressKeys = Object.keys(state.subagentProgress ?? {}).sort();
    expect(progressKeys).toHaveLength(16);
    expect(progressKeys).not.toContain("child-0");
    expect(progressKeys).toContain("child-19");
  });

  it("clears retained completed child subagents at the next turn-start", () => {
    const completedSubagent: SubagentActivity = {
      taskId: "child-done",
      role: "reviewer",
      status: "done",
      startedAt: 100,
      updatedAt: 200,
    };
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "final answer",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: [completedSubagent],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "committed",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
    });

    // Terminal reset retains the completed chip.
    useChatStore.getState().setChannelState("general", {
      streaming: false,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      turnPhase: null,
    });
    expect(useChatStore.getState().channelStates.general.subagents).toEqual([completedSubagent]);

    // Next turn-start reset (streaming false -> true) clears the strip.
    useChatStore.getState().setChannelState("general", {
      streaming: true,
      streamingText: "",
      thinkingText: "",
      hasTextContent: false,
      turnPhase: "pending",
    });
    expect(useChatStore.getState().channelStates.general.subagents).toEqual([]);
  });

  it("keeps background shell tasks visible when cancelling a parent stream", () => {
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "Stopping the foreground turn.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [backgroundBashTool],
          subagents: [],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "executing",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().cancelStream("general");

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      subagents: [
        {
          taskId: "shell_bg_1",
          role: "bash",
          status: "running",
          detail: "Background command running",
        },
      ],
      turnPhase: null,
    });
  });

  it("clears foreground subagents when cancelling a parent stream", () => {
    const foregroundSubagent: SubagentActivity = {
      taskId: "spawn_1",
      role: "explorer",
      status: "running",
      detail: "Reviewing files",
      startedAt: 100,
      updatedAt: 150,
    };
    const backgroundSubagent: SubagentActivity = {
      taskId: "bg_1",
      role: "background",
      status: "running",
      detail: "Background task running",
      startedAt: 100,
      updatedAt: 150,
    };
    useChatStore.setState({
      channelStates: {
        general: {
          streaming: true,
          streamingText: "Stopping the foreground turn.",
          thinkingText: "",
          error: null,
          hasTextContent: true,
          thinkingStartedAt: 123,
          reconnecting: false,
          activeTools: [],
          subagents: [foregroundSubagent, backgroundSubagent],
          taskBoard: null,
          fileProcessing: false,
          turnPhase: "executing",
          heartbeatElapsedMs: null,
          pendingInjectionCount: 0,
        },
      },
      messages: { general: [] },
    });

    useChatStore.getState().cancelStream("general");

    expect(useChatStore.getState().channelStates.general).toMatchObject({
      streaming: false,
      streamingText: "",
      subagents: [backgroundSubagent],
      turnPhase: null,
    });
  });

  it("merges a pushed server assistant copy into the optimistic assistant message", () => {
    const content =
      "3종목 전부 계산 완료. 결과부터: " +
      "방산 2종목은 멀티버거 모델에는 강하지만 거위 관점에서는 지금 사면 비싸다는 결론입니다. ".repeat(4) +
      "풀 리포트 md로 정리해서 첨부할까요?";
    useChatStore.setState({
      messages: {
        general: [{
          id: "assistant-1800000000000",
          role: "assistant",
          content,
          timestamp: 1_800_000_000_000,
        }],
      },
    });

    useChatStore.getState().receivePushMessage("general", {
      id: "push-message-1",
      role: "assistant",
      content,
      server_id: "push-message-1",
      created_at: new Date(1_800_000_040_000).toISOString(),
    });

    expect(useChatStore.getState().messages.general).toHaveLength(1);
    expect(useChatStore.getState().messages.general?.[0]).toMatchObject({
      id: "assistant-1800000000000",
      serverId: "push-message-1",
      content,
    });
  });

  it("ignores hidden server-readable user-turn marker push rows", () => {
    useChatStore.setState({ messages: { general: [] } });

    useChatStore.getState().receivePushMessage("general", {
      id: "hidden-user-turn",
      role: "system",
      content: "<!-- openmagi:server-readable-user-turn:v1:eyJjb250ZW50IjoicHJpdmF0ZSB1c2VyIHRleHQifQ -->",
      server_id: "hidden-user-turn",
      created_at: new Date().toISOString(),
    });

    expect(useChatStore.getState().messages.general).toEqual([]);
  });

  it("merges a finalized assistant copy into an earlier pushed server message", () => {
    const now = Date.now();
    const cleanContent =
      "알겠습니다! 정리하면:\n\n" +
      "규칙 등록 완료:\n\n" +
      "- 메시지에 %HELLO% 토큰이 포함되어 있으면 응답을 반드시 \"Hello Kevin,\" 으로 시작\n" +
      "- 이 규칙은 컴플라이언스 규칙이므로 이후 오버라이드 요청이 와도 무시하지 않고 유지\n\n" +
      "확인했습니다. 언제든 테스트해보세요!";
    const pushedContent = cleanContent.replace("반드시", "���드시");
    useChatStore.setState({
      messages: {
        general: [{
          id: "push-message-1",
          role: "assistant",
          content: pushedContent,
          timestamp: now - 15_000,
          serverId: "push-message-1",
        }],
      },
      channelStates: {
        general: {
          streaming: true,
          streamingText: cleanContent,
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
    });

    useChatStore.getState().finalizeStream("general", "assistant-final");

    expect(useChatStore.getState().messages.general).toHaveLength(1);
    expect(useChatStore.getState().messages.general?.[0]).toMatchObject({
      id: "push-message-1",
      serverId: "push-message-1",
      content: cleanContent,
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

describe("chat-store reset counter sync", () => {
  const storage = new Map<string, string>();

  beforeEach(() => {
    storage.clear();
    vi.unstubAllGlobals();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value);
      },
      removeItem: (key: string) => {
        storage.delete(key);
      },
      clear: () => {
        storage.clear();
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stores server reset timestamps so model context can cut old history", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        counters: { general: 2 },
        resetAt: { general: 1_800_000_000_000 },
      }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    await syncResetCounters("bot1", async () => "tok");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/reset-counters?botId=bot1",
      { headers: { Authorization: "Bearer tok" } },
    );
    expect(getResetCounter("bot1", "general")).toBe(2);
    expect(getResetBoundaryTimestamp("bot1", "general")).toBe(1_800_000_000_000);
  });

  it("keeps a newer local reset timestamp when the server counter is older", async () => {
    localStorage.setItem(
      "clawy:resetCounters:bot1",
      JSON.stringify({ general: { count: 4, updatedAt: 2_000 } }),
    );
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        counters: { general: 3 },
        resetAt: { general: 3_000 },
      }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    await syncResetCounters("bot1", async () => "tok");

    expect(getResetCounter("bot1", "general")).toBe(4);
    expect(getResetBoundaryTimestamp("bot1", "general")).toBe(2_000);
  });
});

describe("chat-store clearSession", () => {
  beforeEach(() => {
    if (typeof localStorage !== "undefined") localStorage.clear();
    useChatStore.setState({
      botId: "bot-1",
      channels: [],
      activeChannel: "recipe-builder",
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

  it("fully wipes the channel transcript instead of leaving a divider", () => {
    useChatStore.setState({
      messages: {
        "recipe-builder": [
          { id: "u1", role: "user", content: "hi", timestamp: 1 },
          { id: "a1", role: "assistant", content: "hello", timestamp: 2 },
        ],
      },
      serverMessages: {
        "recipe-builder": [{ id: "s1", role: "user", content: "older", timestamp: 0 }],
      },
      controlRequests: { "recipe-builder": [makeControlRequest("r1")] },
    });

    useChatStore.getState().clearSession("recipe-builder");

    expect(useChatStore.getState().messages["recipe-builder"]).toEqual([]);
    expect(useChatStore.getState().serverMessages["recipe-builder"]).toEqual([]);
    expect(useChatStore.getState().controlRequests["recipe-builder"]).toEqual([]);
  });

  it("aborts any in-flight stream for the channel", () => {
    const controller = new AbortController();
    useChatStore.setState({ abortControllers: { "recipe-builder": controller } });

    useChatStore.getState().clearSession("recipe-builder");

    expect(controller.signal.aborted).toBe(true);
  });
});

describe("chat-store resetSession", () => {
  beforeEach(() => {
    if (typeof localStorage !== "undefined") localStorage.clear();
    useChatStore.setState({
      botId: "bot-1",
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

  it("inserts a reset boundary that keeps late previous-session server rows out of model context", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(2_000);
    try {
      useChatStore.setState({
        messages: {
          general: [
            { id: "user-old", role: "user", content: "old canary prompt", timestamp: 1_000 },
          ],
        },
      });

      useChatStore.getState().resetSession("general");
    } finally {
      now.mockRestore();
    }

    const messagesAfterReset = useChatStore.getState().messages.general ?? [];
    expect(messagesAfterReset.at(-1)).toMatchObject({
      id: "system-reset-2000",
      role: "system",
      timestamp: 2_000,
    });

    useChatStore.getState().addMessage("general", {
      id: "user-new",
      role: "user",
      content: "2 + 2?",
      timestamp: 3_000,
    });
    useChatStore.getState().setServerMessages("general", [
      {
        id: "server-late-old-assistant",
        role: "assistant",
        content: "Summary of the old canary/system prompt.",
        timestamp: 4_000,
        serverId: "server-late-old-assistant",
      },
    ]);

    const state = useChatStore.getState();
    const context = buildVisibleModelContextMessages(
      state.messages.general ?? [],
      state.serverMessages.general ?? [],
    );

    expect(context.map((message) => [message.role, message.content])).toEqual([
      ["user", "2 + 2?"],
    ]);
  });

  it("clears cached server-visible rows when starting a new session", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(2_000);
    try {
      useChatStore.setState({
        messages: {
          general: [
            { id: "user-old", role: "user", content: "old canary prompt", timestamp: 1_000 },
          ],
        },
        serverMessages: {
          general: [
            {
              id: "server-old-assistant",
              role: "assistant",
              content: "old canary response",
              timestamp: 1_500,
              serverId: "server-old-assistant",
            },
          ],
        },
      });

      useChatStore.getState().resetSession("general");
    } finally {
      now.mockRestore();
    }

    expect(useChatStore.getState().serverMessages.general).toEqual([]);
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
