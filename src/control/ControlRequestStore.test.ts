import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ControlEventLedger } from "./ControlEventLedger.js";
import { ControlRequestStore } from "./ControlRequestStore.js";

async function makeStore(): Promise<ControlRequestStore> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "control-request-"));
  const ledger = new ControlEventLedger({ rootDir: root, sessionKey: "session-a" });
  return new ControlRequestStore({ ledger });
}

describe("ControlRequestStore", () => {
  it("creates durable request ids and resolves idempotently", async () => {
    const store = await makeStore();
    const request = await store.create({
      kind: "tool_permission",
      turnId: "turn-1",
      sessionKey: "session-a",
      channelName: "general",
      source: "turn",
      prompt: "Run Bash?",
      proposedInput: { command: "npm test" },
      expiresAt: Date.now() + 60_000,
    });

    expect(request.requestId).toMatch(/^cr_/);
    expect(await store.resolve(request.requestId, { decision: "approved" })).toMatchObject({
      requestId: request.requestId,
      state: "approved",
    });
    expect(await store.resolve(request.requestId, { decision: "denied" })).toMatchObject({
      requestId: request.requestId,
      state: "approved",
    });
    expect(await store.pending()).toHaveLength(0);
  });

  it("projects pending requests for reconnect hydration", async () => {
    const store = await makeStore();
    await store.create({
      kind: "tool_permission",
      turnId: "turn-1",
      sessionKey: "session-a",
      channelName: "general",
      source: "turn",
      prompt: "Run Bash?",
      proposedInput: { command: "npm test" },
      expiresAt: Date.now() + 60_000,
    });

    expect((await store.project()).pendingRequests).toHaveLength(1);
  });

  it("deduplicates create retries by idempotency key", async () => {
    const store = await makeStore();
    const input = {
      kind: "tool_permission" as const,
      turnId: "turn-1",
      sessionKey: "session-a",
      channelName: "general",
      source: "turn" as const,
      prompt: "Run Bash?",
      proposedInput: { command: "npm test" },
      expiresAt: Date.now() + 60_000,
      idempotencyKey: "turn-1:Bash:1",
    };

    const first = await store.create(input);
    const second = await store.create(input);

    expect(second.requestId).toBe(first.requestId);
    expect((await store.project()).pendingRequests).toHaveLength(1);
  });

  it("projects expired requests as timed out without losing request identity", async () => {
    const store = await makeStore();
    const request = await store.create({
      kind: "tool_permission",
      turnId: "turn-1",
      sessionKey: "session-a",
      channelName: "general",
      source: "turn",
      prompt: "Run Bash?",
      proposedInput: { command: "npm test" },
      expiresAt: Date.now() - 1,
    });

    const projection = await store.project(Date.now());
    expect(projection.pendingRequests).toHaveLength(0);
    expect(projection.requests[request.requestId]).toMatchObject({
      requestId: request.requestId,
      state: "timed_out",
    });
  });
});
