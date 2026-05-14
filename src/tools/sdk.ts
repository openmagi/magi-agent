/**
 * Tool SDK — lightweight helpers for building custom tools.
 *
 * Provides schema builder helpers, result factories, and a test harness
 * so tool authors can build, test, and iterate quickly.
 */

import type {
  Tool,
  ToolContext,
  ToolResult,
  ToolStatus,
  StagingSurface,
} from "../Tool.js";

/* ------------------------------------------------------------------ */
/*  Schema builder helpers                                             */
/* ------------------------------------------------------------------ */

/** String property schema. */
export function stringProp(description: string): object {
  return { type: "string", description };
}

/** Number property schema. */
export function numberProp(description: string): object {
  return { type: "number", description };
}

/** Boolean property schema. */
export function boolProp(description: string): object {
  return { type: "boolean", description };
}

/** Enum property schema. */
export function enumProp(values: string[], description: string): object {
  return { type: "string", enum: values, description };
}

/** Array property schema. */
export function arrayProp(items: object, description: string): object {
  return { type: "array", items, description };
}

/**
 * Build a complete JSON Schema `inputSchema` from a property map.
 *
 * ```ts
 * defineInput({
 *   properties: {
 *     query: stringProp("Search query"),
 *     limit: numberProp("Max results"),
 *   },
 *   required: ["query"],
 * })
 * ```
 */
export function defineInput(schema: {
  properties: Record<string, object>;
  required?: string[];
}): object {
  return {
    type: "object",
    properties: schema.properties,
    required: schema.required ?? Object.keys(schema.properties),
    additionalProperties: false,
  };
}

/* ------------------------------------------------------------------ */
/*  Result factories                                                   */
/* ------------------------------------------------------------------ */

/** Build a successful result with timing. */
export function okResult<T>(output: T, startMs: number): ToolResult<T> {
  return {
    status: "ok" as ToolStatus,
    output,
    durationMs: Date.now() - startMs,
  };
}

/** Build an error result with timing. */
export function errorResult(
  code: string,
  message: string,
  startMs: number,
): ToolResult {
  return {
    status: "error" as ToolStatus,
    errorCode: code,
    errorMessage: message,
    durationMs: Date.now() - startMs,
  };
}

/* ------------------------------------------------------------------ */
/*  Test harness                                                       */
/* ------------------------------------------------------------------ */

/**
 * Create a minimal ToolContext suitable for unit testing custom tools.
 * Override any field via the `overrides` parameter.
 */
export function createTestHarness(
  overrides?: Partial<ToolContext>,
): {
  run<I, O>(tool: Tool<I, O>, input: I): Promise<ToolResult<O>>;
  ctx: ToolContext;
} {
  const staging: StagingSurface = {
    stageFileWrite: () => {},
    stageTranscriptAppend: () => {},
    stageAuditEvent: () => {},
  };

  const ctx: ToolContext = {
    botId: "test-bot",
    sessionKey: "test-session",
    turnId: "test-turn",
    workspaceRoot: process.cwd(),
    askUser: async () => ({}),
    emitProgress: () => {},
    abortSignal: new AbortController().signal,
    staging,
    ...overrides,
  };

  return {
    ctx,
    async run<I, O>(tool: Tool<I, O>, input: I): Promise<ToolResult<O>> {
      if (tool.validate) {
        const err = tool.validate(input);
        if (err) {
          return {
            status: "error",
            errorCode: "validation_error",
            errorMessage: err,
            durationMs: 0,
          };
        }
      }
      return tool.execute(input, ctx);
    },
  };
}
