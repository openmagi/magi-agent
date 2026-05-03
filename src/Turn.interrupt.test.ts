import { describe, expect, it } from "vitest";
import type { ServerResponse } from "node:http";
import { Turn, TurnInterruptedError } from "./Turn.js";
import { SseWriter } from "./transport/SseWriter.js";
import type { Session } from "./Session.js";
import type { UserMessage } from "./util/types.js";

class FakeSse extends SseWriter {
  readonly events: Array<Record<string, unknown>> = [];
  constructor() {
    super({
      writeHead: () => {},
      write: () => true,
      end: () => {},
    } as unknown as ServerResponse);
  }
  override agent(event: unknown): void {
    this.events.push(event as Record<string, unknown>);
  }
  override legacyDelta(): void {}
  override legacyFinish(): void {}
  override start(): void {}
  override end(): void {}
}

function makeTurn(): { turn: Turn; sse: FakeSse } {
  const sse = new FakeSse();
  const session = {
    meta: { sessionKey: "agent:main:app:general" },
    agent: { config: { model: "claude-test" } },
  } as unknown as Session;
  const userMessage: UserMessage = {
    text: "continue",
    receivedAt: Date.now(),
  };
  const turn = new Turn(session, userMessage, "turn-1", sse, "direct");
  return { turn, sse };
}

describe("Turn.requestInterrupt", () => {
  it("emits a structured interrupt acknowledgement for live clients", () => {
    const { turn, sse } = makeTurn();

    const result = turn.requestInterrupt(true, "web");

    expect(result).toEqual({ status: "accepted", handoffRequested: true });
    expect(sse.events).toContainEqual({
      type: "turn_interrupted",
      turnId: "turn-1",
      handoffRequested: true,
      source: "web",
    });
    expect(() => turn.assertNotInterrupted()).toThrow(TurnInterruptedError);
  });
});
