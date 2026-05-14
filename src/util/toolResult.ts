/**
 * Tool-result helpers used across every Tool.execute() and by Turn.ts when
 * rendering tool_result blocks for the LLM / SSE activity previews.
 *
 * Extracted from src/tools/FileRead.ts and src/Turn.ts (pure relocation,
 * no semantic change) so FileRead is no longer an ambient util namespace.
 */

import type { ToolResult } from "../Tool.js";

/**
 * Construct a ToolResult<never> from a thrown error. Preserves the
 * error's `code` (NodeJS.ErrnoException) or `name` when available so
 * callers can distinguish ENOENT / EACCES / etc. from generic errors.
 */
export function errorResult(err: unknown, startedAt: number): ToolResult<never> {
  const msg = err instanceof Error ? err.message : String(err);
  const code =
    (err as NodeJS.ErrnoException)?.code ??
    (err as { name?: string })?.name ??
    "error";
  return {
    status: "error",
    errorCode: code,
    errorMessage: msg,
    durationMs: Date.now() - startedAt,
  };
}

/**
 * Render a ToolResult into the text the LLM sees inside a tool_result
 * content block. String outputs pass through; objects are JSON-encoded;
 * errors become `error:<code> <message>` so models can recover.
 */
export function summariseToolOutput(result: ToolResult): string {
  if (result.status === "ok") {
    const out = result.output;
    if (out === undefined) return "ok";
    if (typeof out === "string") return out;
    try {
      return JSON.stringify(out);
    } catch {
      return String(out);
    }
  }
  const code = result.errorCode ?? result.status;
  const msg = result.errorMessage ?? "";
  return msg ? `error:${code} ${msg}` : `error:${code}`;
}

/**
 * Build a compact preview of an arbitrary tool input value for display
 * in the client activity card. Keeps JSON shape where possible, truncates
 * at ~400 chars. Used by tool_start + tool_end AgentEvents.
 */
export function buildPreview(input: unknown): string {
  try {
    const s = typeof input === "string" ? input : JSON.stringify(input, null, 2);
    return s.length > 400 ? `${s.slice(0, 400)}...` : s;
  } catch {
    return "<unstringifiable>";
  }
}

function cleanPromptLine(line: string): string {
  return line
    .trim()
    .replace(/^#+\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/^[-*]\s*/, "")
    .trim();
}

function bounded(value: string, maxLength: number): string {
  const clean = value.trim();
  if (clean.length <= maxLength) return clean;
  return `${clean.slice(0, Math.max(0, maxLength - 3)).trimEnd()}...`;
}

export function summariseDelegatedPrompt(
  prompt?: string,
  maxLength = 240,
): string | undefined {
  if (!prompt) return undefined;
  const lines = prompt
    .split(/\r?\n/)
    .map(cleanPromptLine)
    .filter(Boolean);
  const taskLine = lines.find((line) =>
    /^(task|request|work order|작업|요청)\s*:/i.test(line),
  );
  const goalLine = lines.find((line) => /^(goal|objective|목표)\s*:/i.test(line));
  const firstNonPersonaLine = lines.find((line) => !/^you are\b/i.test(line));
  const title = taskLine ?? goalLine ?? firstNonPersonaLine ?? lines[0];
  if (!title) return undefined;
  const objective = goalLine && goalLine !== title ? goalLine : undefined;
  return bounded([title, objective].filter(Boolean).join("\n"), maxLength);
}

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function promptField(input: Record<string, unknown>): string | undefined {
  for (const key of ["prompt", "task", "instructions", "message"]) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

export function buildToolInputPreview(toolName: string, input: unknown): string {
  const normalized = toolName.replace(/[^a-z0-9]/gi, "").toLowerCase();
  if (normalized === "spawnagent") {
    const summary = summariseDelegatedPrompt(
      promptField(recordFromUnknown(input) ?? {}),
    );
    if (summary) return JSON.stringify({ prompt: summary });
  }
  return buildPreview(input);
}
