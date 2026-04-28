import { describe, expect, it } from "vitest";
import { StructuredOutputContract } from "./StructuredOutputContract.js";

describe("StructuredOutputContract", () => {
  const schema = {
    type: "object",
    required: ["summary", "score"],
    properties: {
      summary: { type: "string" },
      score: { type: "number" },
    },
  } as const;

  it("validates JSON text against a small schema subset", () => {
    const contract = new StructuredOutputContract({ schemaName: "verdict", schema });
    expect(contract.validate('{"summary":"ok","score":1}')).toMatchObject({ ok: true });
    expect(contract.validate('{"summary":"ok","score":"bad"}')).toMatchObject({
      ok: false,
      reason: expect.stringContaining("score"),
    });
  });

  it("extracts fenced JSON before validation", () => {
    const contract = new StructuredOutputContract({ schemaName: "verdict", schema });
    expect(
      contract.validate('```json\n{"summary":"ok","score":1}\n```'),
    ).toMatchObject({ ok: true });
  });

  it("emits invalid and retry-exhausted events through RetryController", async () => {
    const contract = new StructuredOutputContract({
      schemaName: "verdict",
      schema,
      maxAttempts: 2,
    });
    const controlEvents: unknown[] = [];
    const agentEvents: unknown[] = [];

    const first = await contract.assess({
      text: '{"summary":"ok","score":"bad"}',
      turnId: "turn-1",
      attempt: 1,
      emitControlEvent: async (event) => {
        controlEvents.push(event);
      },
      emitAgentEvent: (event) => {
        agentEvents.push(event);
      },
    });

    expect(first).toMatchObject({
      ok: false,
      status: "invalid",
      retry: { action: "resample" },
    });
    expect(controlEvents[0]).toMatchObject({
      type: "structured_output",
      status: "invalid",
      schemaName: "verdict",
    });
    expect(agentEvents[0]).toMatchObject({
      type: "structured_output",
      status: "invalid",
    });

    const second = await contract.assess({
      text: '{"summary":"ok","score":"bad"}',
      turnId: "turn-1",
      attempt: 2,
      emitControlEvent: async (event) => {
        controlEvents.push(event);
      },
      emitAgentEvent: (event) => {
        agentEvents.push(event);
      },
    });

    expect(second).toMatchObject({
      ok: false,
      status: "retry_exhausted",
      retry: { action: "abort" },
    });
    expect(controlEvents[1]).toMatchObject({
      type: "structured_output",
      status: "retry_exhausted",
    });
    expect(agentEvents[1]).toMatchObject({
      type: "structured_output",
      status: "retry_exhausted",
    });
  });
});
