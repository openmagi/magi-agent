import { describe, expect, it } from "vitest";

import {
  channelMemoryPolicyFromSessionKey,
  memoryModeFromChannelPolicy,
  shouldSkipMemoryWriteForSession,
} from "./ChannelMemoryPolicy.js";

describe("channel memory policy", () => {
  it.each([
    ["agent:local:app:research-read-only-memory", "read_only"],
    ["agent:local:app:research-readonly-memory:2", "read_only"],
    ["agent:local:app:research-no-memory", "disabled"],
    ["agent:local:app:memory-off-research:1", "disabled"],
  ] as const)("detects %s as %s", (sessionKey, expected) => {
    expect(channelMemoryPolicyFromSessionKey(sessionKey)).toBe(expected);
  });

  it("ignores ordinary channels and non-app session keys", () => {
    expect(channelMemoryPolicyFromSessionKey("agent:local:app:general")).toBeNull();
    expect(channelMemoryPolicyFromSessionKey("agent:local:telegram:1234")).toBeNull();
  });

  it("maps policy labels to runtime memory modes", () => {
    expect(memoryModeFromChannelPolicy("read_only")).toBe("read_only");
    expect(memoryModeFromChannelPolicy("disabled")).toBe("incognito");
    expect(memoryModeFromChannelPolicy(null)).toBeUndefined();
  });

  it("skips memory writes for read-only and disabled memory channels", () => {
    expect(shouldSkipMemoryWriteForSession("agent:local:app:memo-read-only-memory")).toBe(true);
    expect(shouldSkipMemoryWriteForSession("agent:local:app:scratch-no-memory")).toBe(true);
    expect(shouldSkipMemoryWriteForSession("agent:local:app:general")).toBe(false);
  });
});
