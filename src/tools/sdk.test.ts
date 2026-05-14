/**
 * sdk.test — schema helpers, result factories, test harness.
 */

import { describe, it, expect } from "vitest";

import {
  stringProp,
  numberProp,
  boolProp,
  enumProp,
  arrayProp,
  defineInput,
  okResult,
  errorResult,
  createTestHarness,
} from "./sdk.js";
import type { Tool } from "../Tool.js";

describe("Schema helpers", () => {
  it("stringProp creates string schema", () => {
    const prop = stringProp("A string field");
    expect(prop).toEqual({ type: "string", description: "A string field" });
  });

  it("numberProp creates number schema", () => {
    const prop = numberProp("A number field");
    expect(prop).toEqual({ type: "number", description: "A number field" });
  });

  it("boolProp creates boolean schema", () => {
    const prop = boolProp("A bool field");
    expect(prop).toEqual({ type: "boolean", description: "A bool field" });
  });

  it("enumProp creates enum schema", () => {
    const prop = enumProp(["a", "b", "c"], "Choose one");
    expect(prop).toEqual({
      type: "string",
      enum: ["a", "b", "c"],
      description: "Choose one",
    });
  });

  it("arrayProp creates array schema", () => {
    const prop = arrayProp({ type: "string" }, "List of strings");
    expect(prop).toEqual({
      type: "array",
      items: { type: "string" },
      description: "List of strings",
    });
  });

  it("defineInput builds complete inputSchema", () => {
    const schema = defineInput({
      properties: {
        query: stringProp("Search query"),
        limit: numberProp("Max results"),
      },
      required: ["query"],
    });

    expect(schema).toEqual({
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        limit: { type: "number", description: "Max results" },
      },
      required: ["query"],
      additionalProperties: false,
    });
  });

  it("defineInput defaults required to all properties", () => {
    const schema = defineInput({
      properties: {
        a: stringProp("A"),
        b: numberProp("B"),
      },
    }) as Record<string, unknown>;

    expect(schema.required).toEqual(["a", "b"]);
  });
});

describe("Result factories", () => {
  it("okResult creates successful result with timing", () => {
    const startMs = Date.now() - 100;
    const result = okResult({ value: 42 }, startMs);

    expect(result.status).toBe("ok");
    expect(result.output).toEqual({ value: 42 });
    expect(result.durationMs).toBeGreaterThanOrEqual(100);
  });

  it("errorResult creates error result with code and message", () => {
    const startMs = Date.now();
    const result = errorResult("not_found", "Resource missing", startMs);

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("not_found");
    expect(result.errorMessage).toBe("Resource missing");
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });
});

describe("Test harness", () => {
  it("provides a complete ToolContext", () => {
    const harness = createTestHarness();
    expect(harness.ctx.botId).toBe("test-bot");
    expect(harness.ctx.sessionKey).toBe("test-session");
    expect(harness.ctx.turnId).toBe("test-turn");
    expect(typeof harness.ctx.workspaceRoot).toBe("string");
  });

  it("allows overriding context fields", () => {
    const harness = createTestHarness({
      botId: "custom-bot",
      workspaceRoot: "/custom/root",
    });
    expect(harness.ctx.botId).toBe("custom-bot");
    expect(harness.ctx.workspaceRoot).toBe("/custom/root");
  });

  it("runs a tool and returns result", async () => {
    const tool: Tool<{ n: number }, { doubled: number }> = {
      name: "Double",
      description: "Doubles a number",
      permission: "read",
      inputSchema: {
        type: "object",
        properties: { n: { type: "number" } },
        required: ["n"],
      },
      async execute(input) {
        return {
          status: "ok",
          output: { doubled: input.n * 2 },
          durationMs: 0,
        };
      },
    };

    const harness = createTestHarness();
    const result = await harness.run(tool, { n: 21 });

    expect(result.status).toBe("ok");
    expect(result.output?.doubled).toBe(42);
  });

  it("runs validation before execute", async () => {
    const tool: Tool<{ n: number }, { doubled: number }> = {
      name: "ValidateDouble",
      description: "Doubles with validation",
      permission: "read",
      inputSchema: {
        type: "object",
        properties: { n: { type: "number" } },
        required: ["n"],
      },
      validate(input) {
        if (input.n < 0) return "n must be non-negative";
        return null;
      },
      async execute(input) {
        return {
          status: "ok",
          output: { doubled: input.n * 2 },
          durationMs: 0,
        };
      },
    };

    const harness = createTestHarness();
    const result = await harness.run(tool, { n: -1 });

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("validation_error");
    expect(result.errorMessage).toBe("n must be non-negative");
  });
});
