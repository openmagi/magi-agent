import type { Tool, ToolContext, ToolResult } from "../Tool.js";

export type CalculationOperation =
  | "sum"
  | "average"
  | "count"
  | "min"
  | "max"
  | "percent_change"
  | "group_by_sum";

export interface CalculationInput {
  operation: CalculationOperation;
  rows: Array<Record<string, unknown>>;
  field?: string;
  groupBy?: string;
  before?: number | string;
  after?: number | string;
  requirementId?: string;
  resourceIds?: string[];
}

export interface CalculationOutput {
  operation: CalculationOperation;
  field?: string;
  groupBy?: string;
  result: unknown;
  rowCount: number;
  numericCount: number;
  ignoredCount: number;
  sum?: number;
  min?: number;
  max?: number;
  before?: number;
  after?: number;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    operation: {
      type: "string",
      enum: ["sum", "average", "count", "min", "max", "percent_change", "group_by_sum"],
    },
    rows: { type: "array", items: { type: "object" } },
    field: { type: "string" },
    groupBy: { type: "string" },
    before: { oneOf: [{ type: "number" }, { type: "string" }] },
    after: { oneOf: [{ type: "number" }, { type: "string" }] },
    requirementId: { type: "string" },
    resourceIds: { type: "array", items: { type: "string" } },
  },
  required: ["operation", "rows"],
  additionalProperties: false,
} as const;

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value.trim())) {
    const n = Number(value.trim());
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function valuesFor(rows: Array<Record<string, unknown>>, field: string): number[] {
  return rows
    .map((row) => finiteNumber(row[field]))
    .filter((value): value is number => value !== null);
}

function sum(values: number[]): number {
  return values.reduce((acc, value) => acc + value, 0);
}

function validateInput(input: CalculationInput): string | null {
  if (!input || typeof input !== "object") return "`input` must be an object";
  if (!Array.isArray(input.rows)) return "`rows` must be an array";
  if (!input.operation) return "`operation` is required";
  if (input.operation === "percent_change") {
    if (finiteNumber(input.before) === null || finiteNumber(input.after) === null) {
      return "`before` and `after` numeric values are required for percent_change";
    }
    if (finiteNumber(input.before) === 0) {
      return "`before` cannot be zero for percent_change";
    }
    return null;
  }
  if (!input.field) return "`field` is required for this operation";
  if (input.operation === "group_by_sum" && !input.groupBy) {
    return "`groupBy` is required for group_by_sum";
  }
  return null;
}

function calculate(input: CalculationInput): CalculationOutput {
  if (input.operation === "percent_change") {
    const before = finiteNumber(input.before)!;
    const after = finiteNumber(input.after)!;
    return {
      operation: input.operation,
      result: ((after - before) / before) * 100,
      rowCount: input.rows.length,
      numericCount: 2,
      ignoredCount: 0,
      before,
      after,
    };
  }
  if (input.operation === "count") {
    const numericValues = input.field ? valuesFor(input.rows, input.field) : [];
    const result = input.field ? numericValues.length : input.rows.length;
    return {
      operation: input.operation,
      field: input.field,
      result,
      rowCount: input.rows.length,
      numericCount: input.field ? numericValues.length : input.rows.length,
      ignoredCount: input.field ? input.rows.length - numericValues.length : 0,
    };
  }
  if (input.operation === "group_by_sum") {
    const groups: Record<string, number> = {};
    let numericCount = 0;
    for (const row of input.rows) {
      const value = finiteNumber(row[input.field!]);
      if (value === null) continue;
      numericCount += 1;
      const key = String(row[input.groupBy!] ?? "");
      groups[key] = (groups[key] ?? 0) + value;
    }
    return {
      operation: input.operation,
      field: input.field,
      groupBy: input.groupBy,
      result: groups,
      rowCount: input.rows.length,
      numericCount,
      ignoredCount: input.rows.length - numericCount,
    };
  }
  const values = valuesFor(input.rows, input.field!);
  const total = sum(values);
  const base: CalculationOutput = {
    operation: input.operation,
    field: input.field,
    result: null,
    rowCount: input.rows.length,
    numericCount: values.length,
    ignoredCount: input.rows.length - values.length,
    sum: total,
  };
  if (input.operation === "sum") return { ...base, result: total };
  if (input.operation === "average") {
    return { ...base, result: values.length === 0 ? null : total / values.length };
  }
  if (input.operation === "min") {
    const min = values.length === 0 ? undefined : Math.min(...values);
    return { ...base, result: min ?? null, min };
  }
  const max = values.length === 0 ? undefined : Math.max(...values);
  return { ...base, result: max ?? null, max };
}

function activeRequirementIdsForCalculation(ctx: ToolContext): string[] {
  const snapshot = ctx.executionContract?.snapshot();
  if (!snapshot) return [];
  return snapshot.taskState.deterministicRequirements
    .filter(
      (requirement) =>
        requirement.status === "active" &&
        requirement.kinds.some((kind) =>
          kind === "calculation" ||
          kind === "counting" ||
          kind === "comparison",
        ),
    )
    .map((requirement) => requirement.requirementId);
}

export function makeCalculationTool(): Tool<CalculationInput, CalculationOutput> {
  return {
    name: "Calculation",
    description:
      "Run deterministic arithmetic over structured rows. Use for sums, averages, counts, min/max, percent change, and grouped sums instead of mental math.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    validate: validateInput,
    async execute(input: CalculationInput, ctx: ToolContext): Promise<ToolResult<CalculationOutput>> {
      const started = Date.now();
      const validation = validateInput(input);
      if (validation) {
        return {
          status: "error",
          errorCode: "invalid_calculation_input",
          errorMessage: validation,
          durationMs: Date.now() - started,
        };
      }
      const output = calculate(input);
      const requirementIds = input.requirementId
        ? [input.requirementId]
        : activeRequirementIdsForCalculation(ctx);
      const resources = input.resourceIds ?? [];
      ctx.executionContract?.recordDeterministicEvidence({
        evidenceId: `de_calc_${ctx.turnId}_${input.operation}_${Date.now().toString(36)}`,
        turnId: ctx.turnId,
        requirementIds,
        toolName: "Calculation",
        kind: "calculation",
        status: "passed",
        inputSummary: `${input.operation} ${input.field ?? ""}`.trim(),
        output,
        assertions: [
          `rowCount=${output.rowCount}`,
          `numericCount=${output.numericCount}`,
          `ignoredCount=${output.ignoredCount}`,
          `result=${JSON.stringify(output.result)}`,
        ],
        resources,
      });
      return {
        status: "ok",
        output,
        durationMs: Date.now() - started,
        metadata: { deterministicEvidence: true, resources },
      };
    },
  };
}
