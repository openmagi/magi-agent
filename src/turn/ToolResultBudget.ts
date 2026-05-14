/**
 * Layer 1 — Tool Result Budget.
 * Caps individual tool_result content blocks before they enter the
 * in-memory messages array. Prevents single outsized results from
 * blowing the context window.
 */

export interface ToolResultBudgetConfig {
  maxResultSizeChars: number;
  exemptTools: Set<string>;
  headChars: number;
  tailChars: number;
}

export const DEFAULT_TOOL_RESULT_BUDGET_CONFIG: ToolResultBudgetConfig = {
  maxResultSizeChars: parseInt(process.env.MAGI_TOOL_RESULT_BUDGET_MAX_CHARS ?? "100000", 10),
  exemptTools: new Set<string>(),
  headChars: 2000,
  tailChars: 2000,
};

export function applyToolResultBudget(
  content: string,
  toolName: string,
  config: ToolResultBudgetConfig = DEFAULT_TOOL_RESULT_BUDGET_CONFIG,
): string {
  if (config.maxResultSizeChars <= 0) return content;
  if (content.length <= config.maxResultSizeChars) return content;
  if (config.exemptTools.has(toolName)) return content;

  const truncated = tryJsonArrayTruncation(content, config.maxResultSizeChars);
  if (truncated !== null) return truncated;

  return textTruncation(content, config);
}

function textTruncation(content: string, config: ToolResultBudgetConfig): string {
  const { maxResultSizeChars, headChars, tailChars } = config;
  const omitted = content.length - headChars - tailChars;
  const head = content.slice(0, headChars);
  const tail = content.slice(-tailChars);
  const marker = `\n...[${omitted} chars omitted]...\n`;
  const header = `[Tool result truncated from ${content.length} to ~${maxResultSizeChars} chars. Head and tail preserved.]\n`;
  return (header + head + marker + tail).slice(0, maxResultSizeChars);
}

function tryJsonArrayTruncation(content: string, maxChars: number): string | null {
  const trimmed = content.trimStart();
  if (!trimmed.startsWith("[")) return null;
  let arr: unknown[];
  try {
    arr = JSON.parse(content);
  } catch {
    return null;
  }
  if (!Array.isArray(arr) || arr.length === 0) return null;

  let lo = 1;
  let hi = arr.length;
  let best = 1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    const candidate = JSON.stringify(arr.slice(0, mid));
    if (candidate.length <= maxChars) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  const sliced = arr.slice(0, best);
  const result = JSON.stringify(sliced);
  if (best < arr.length) {
    const suffix = `\n[... ${arr.length - best} of ${arr.length} elements omitted]`;
    return (result + suffix).slice(0, maxChars);
  }
  return result;
}
