import { describe, expect, it, vi } from "vitest";

import { persistUserHistoryMessage } from "./user-history-persistence";
import type { ChatMessage } from "./types";

describe("persistUserHistoryMessage", () => {
  it("persists new user messages even before legacy E2EE keys are ready", () => {
    const saveMessages = vi.fn().mockResolvedValue(undefined);
    const message: ChatMessage = {
      id: "user-1",
      role: "user",
      content: "hello",
      timestamp: 1,
    };

    persistUserHistoryMessage({
      e2eeReady: false,
      saveMessages,
      channel: "general",
      message,
    });

    expect(saveMessages).toHaveBeenCalledWith("general", [
      {
        role: "user",
        content: "hello",
        clientMsgId: "user-1",
      },
    ]);
  });

  it("still skips non-user messages", () => {
    const saveMessages = vi.fn().mockResolvedValue(undefined);
    const message: ChatMessage = {
      id: "assistant-1",
      role: "assistant",
      content: "hello",
      timestamp: 1,
    };

    persistUserHistoryMessage({
      e2eeReady: false,
      saveMessages,
      channel: "general",
      message,
    });

    expect(saveMessages).not.toHaveBeenCalled();
  });
});
