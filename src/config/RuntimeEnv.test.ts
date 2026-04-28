import { afterEach, describe, expect, it, vi } from "vitest";
import { loadRuntimeEnv } from "./RuntimeEnv.js";

const REQUIRED_ENV = {
  BOT_ID: "bot",
  USER_ID: "user",
  GATEWAY_TOKEN: "gateway",
  CORE_AGENT_API_PROXY_URL: "http://api-proxy",
  CORE_AGENT_CHAT_PROXY_URL: "http://chat-proxy",
  CORE_AGENT_REDIS_URL: "redis://localhost:6379",
};

describe("loadRuntimeEnv", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("preserves hosted UX by making bypass an explicit env-mode default", () => {
    for (const [key, value] of Object.entries(REQUIRED_ENV)) {
      vi.stubEnv(key, value);
    }

    const env = loadRuntimeEnv();

    expect(env.agentConfig.defaultPermissionMode).toBe("bypass");
  });

  it("accepts CORE_AGENT_PERMISSION_MODE as an explicit override", () => {
    for (const [key, value] of Object.entries(REQUIRED_ENV)) {
      vi.stubEnv(key, value);
    }
    vi.stubEnv("CORE_AGENT_PERMISSION_MODE", "auto");

    const env = loadRuntimeEnv();

    expect(env.agentConfig.defaultPermissionMode).toBe("auto");
  });
});
