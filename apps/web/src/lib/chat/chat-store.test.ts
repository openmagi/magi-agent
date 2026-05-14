import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "./chat-store";
import type { ChatMessage } from "./types";

describe("chat-store selection mode", () => {
  beforeEach(() => {
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

  it("keeps bounded runtime trace rows from direct or replayed control events", () => {
    for (let index = 0; index < 14; index += 1) {
      useChatStore.getState().applyControlEvent("general", {
        type: "runtime_trace",
        turnId: "turn-1",
        phase: "verifier_blocked",
        severity: index % 2 === 0 ? "warning" : "error",
        title: `Runtime verifier blocked completion ${index}`,
        reasonCode: "ARTIFACT_DELIVERY_REQUIRED",
      });
    }

    const traces = useChatStore.getState().channelStates.general?.runtimeTraces ?? [];
    expect(traces).toHaveLength(12);
    expect(traces[0]?.title).toBe("Runtime verifier blocked completion 2");
    expect(traces[11]).toMatchObject({
      phase: "verifier_blocked",
      severity: "error",
      reasonCode: "ARTIFACT_DELIVERY_REQUIRED",
    });
  });
});
