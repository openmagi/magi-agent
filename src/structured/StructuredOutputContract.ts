import { RetryController, type RetryDecision } from "../turn/RetryController.js";
import type { ControlEventInput } from "../control/ControlEvents.js";
import type { AgentEvent } from "../transport/SseWriter.js";

export interface JsonSchemaLite {
  type?: string | readonly string[];
  required?: readonly string[];
  properties?: Record<string, JsonSchemaLite>;
  items?: JsonSchemaLite;
  enum?: readonly unknown[];
}

export interface StructuredOutputSpec {
  schemaName?: string;
  schema: JsonSchemaLite;
  maxAttempts?: number;
}

export type StructuredOutputStatus = "valid" | "invalid" | "retry_exhausted";

export type StructuredOutputValidation =
  | { ok: true; value: unknown }
  | { ok: false; reason: string };

export type StructuredOutputAssessment =
  | {
      ok: true;
      status: "valid";
      value: unknown;
    }
  | {
      ok: false;
      status: "invalid" | "retry_exhausted";
      reason: string;
      retry: RetryDecision;
    };

export class StructuredOutputContract {
  readonly schemaName: string | undefined;
  readonly schema: JsonSchemaLite;
  readonly maxAttempts: number;

  constructor(spec: StructuredOutputSpec) {
    this.schemaName = spec.schemaName;
    this.schema = spec.schema;
    this.maxAttempts = spec.maxAttempts ?? 3;
  }

  validate(text: string): StructuredOutputValidation {
    const parsed = parseJsonText(text);
    if (!parsed.ok) return parsed;
    const reason = validateValue(parsed.value, this.schema, "$");
    if (reason) return { ok: false, reason };
    return { ok: true, value: parsed.value };
  }

  async assess(input: {
    text: string;
    turnId: string;
    attempt: number;
    emitControlEvent?: (event: ControlEventInput) => Promise<unknown>;
    emitAgentEvent?: (event: AgentEvent) => void;
  }): Promise<StructuredOutputAssessment> {
    const validation = this.validate(input.text);
    if (validation.ok) {
      await this.emit(input, "valid");
      return { ok: true, status: "valid", value: validation.value };
    }

    const retry = new RetryController({ maxAttempts: this.maxAttempts }).next({
      kind: "structured_output_invalid",
      reason: validation.reason,
      attempt: input.attempt,
    });
    const status: "invalid" | "retry_exhausted" =
      retry.action === "abort" ? "retry_exhausted" : "invalid";
    await this.emit(input, status, validation.reason);
    return {
      ok: false,
      status,
      reason: validation.reason,
      retry,
    };
  }

  private async emit(
    input: {
      turnId: string;
      emitControlEvent?: (event: ControlEventInput) => Promise<unknown>;
      emitAgentEvent?: (event: AgentEvent) => void;
    },
    status: StructuredOutputStatus,
    reason?: string,
  ): Promise<void> {
    const event = {
      type: "structured_output" as const,
      turnId: input.turnId,
      status,
      ...(this.schemaName ? { schemaName: this.schemaName } : {}),
      ...(reason ? { reason } : {}),
    };
    input.emitAgentEvent?.(event);
    await input.emitControlEvent?.(event);
  }
}

function parseJsonText(text: string): StructuredOutputValidation {
  const candidate = extractJsonCandidate(text);
  try {
    return { ok: true, value: JSON.parse(candidate) };
  } catch (err) {
    return {
      ok: false,
      reason: `invalid JSON: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

function extractJsonCandidate(text: string): string {
  const trimmed = text.trim();
  const fence = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence?.[1]) return fence[1].trim();
  return trimmed;
}

function validateValue(
  value: unknown,
  schema: JsonSchemaLite | undefined,
  path: string,
): string | null {
  if (!schema) return null;

  if (schema.enum && !schema.enum.some((candidate) => candidate === value)) {
    return `${path} must be one of ${schema.enum.map(String).join(", ")}`;
  }

  const typeError = validateType(value, schema.type, path);
  if (typeError) return typeError;

  if (schema.type === "object" || (!schema.type && schema.properties)) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return `${path} must be object`;
    }
    const obj = value as Record<string, unknown>;
    for (const key of schema.required ?? []) {
      if (!(key in obj)) return `${path}.${key} is required`;
    }
    for (const [key, childSchema] of Object.entries(schema.properties ?? {})) {
      if (!(key in obj)) continue;
      const childError = validateValue(obj[key], childSchema, `${path}.${key}`);
      if (childError) return childError;
    }
  }

  if (schema.type === "array" && schema.items && Array.isArray(value)) {
    for (let i = 0; i < value.length; i += 1) {
      const childError = validateValue(value[i], schema.items, `${path}[${i}]`);
      if (childError) return childError;
    }
  }

  return null;
}

function validateType(
  value: unknown,
  rawType: JsonSchemaLite["type"],
  path: string,
): string | null {
  if (!rawType) return null;
  const types = Array.isArray(rawType) ? rawType : [rawType];
  if (types.some((type) => valueMatchesType(value, type))) return null;
  return `${path} must be ${types.join(" or ")}`;
}

function valueMatchesType(value: unknown, type: string): boolean {
  switch (type) {
    case "object":
      return !!value && typeof value === "object" && !Array.isArray(value);
    case "array":
      return Array.isArray(value);
    case "string":
      return typeof value === "string";
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "integer":
      return Number.isInteger(value);
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    default:
      return true;
  }
}
