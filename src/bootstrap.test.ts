import { describe, expect, it } from "vitest";

import { bootstrapCoreAgent } from "./bootstrap.js";

describe("bootstrapCoreAgent", () => {
  it("starts HTTP health before awaiting long agent startup work", async () => {
    const events: string[] = [];
    let releaseAgentStart!: () => void;
    const agentStart = new Promise<void>((resolve) => {
      releaseAgentStart = resolve;
    });

    const started = bootstrapCoreAgent({
      agent: {
        start: async () => {
          events.push("agent:start");
          await agentStart;
          events.push("agent:started");
        },
        stop: async () => {
          events.push("agent:stop");
        },
      },
      http: {
        start: async () => {
          events.push("http:start");
        },
        stop: async () => {
          events.push("http:stop");
        },
      },
    });

    await Promise.resolve();

    expect(events).toEqual(["http:start", "agent:start"]);

    releaseAgentStart();
    await started;
  });
});
