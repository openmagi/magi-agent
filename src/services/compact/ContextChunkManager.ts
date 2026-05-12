import type { LLMMessage, LLMToolDef } from "../../transport/LLMClient.js";

export type ChunkCategory =
  | "system"
  | "tools"
  | "memory"
  | "workspace"
  | "active_files"
  | "history_recent"
  | "history_old"
  | "repo_context"
  | "examples"
  | "reminder";

export interface ContextChunk {
  id: string;
  priority: number;
  category: ChunkCategory;
  content: string;
  tokenCount: number;
  shrinkable: boolean;
  minTokens?: number;
}

export interface DroppedContextChunk {
  id: string;
  priority: number;
  category: ChunkCategory;
  tokenCount: number;
  reason: "budget_exceeded" | "mandatory_overflow";
}

export interface BudgetAllocation {
  totalBudget: number;
  usedTokens: number;
  overBudget: boolean;
  includedChunks: ContextChunk[];
  droppedChunks: DroppedContextChunk[];
  warnings: string[];
}

export interface HistorySplit {
  old: LLMMessage[];
  recent: LLMMessage[];
}

export interface AllocatedHistory {
  messages: LLMMessage[];
  allocation: BudgetAllocation;
  oldTokenCount: number;
  recentTokenCount: number;
}

const CATEGORY_PRIORITIES: Record<ChunkCategory, number> = {
  system: 0,
  tools: 1,
  memory: 2,
  workspace: 3,
  active_files: 4,
  history_recent: 5,
  history_old: 6,
  repo_context: 7,
  examples: 8,
  reminder: 9,
};

const CHARS_PER_TOKEN = 4;
const TRUNCATION_MARKER = "\n...[chunk truncated by priority context budget]";

const SYSTEM_BLOCK_SPECS: Array<{
  category: Exclude<ChunkCategory, "tools" | "history_recent" | "history_old" | "active_files" | "examples" | "reminder">;
  id: string;
  tags: string[];
  shrinkable: boolean;
  minTokens?: number;
}> = [
  {
    category: "memory",
    id: "memory_context",
    tags: ["memory-continuity-policy", "memory-root", "memory-context"],
    shrinkable: true,
    minTokens: 256,
  },
  {
    category: "workspace",
    id: "workspace_snapshot",
    tags: ["workspace_snapshot"],
    shrinkable: true,
    minTokens: 192,
  },
  {
    category: "repo_context",
    id: "repo_context",
    tags: ["repo_map"],
    shrinkable: true,
    minTokens: 256,
  },
];

export function estimateTextTokens(text: string): number {
  if (text.length === 0) return 0;
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

export function estimateMessagesTokens(messages: readonly LLMMessage[]): number {
  let total = 0;
  for (const message of messages) {
    total += estimateTextTokens(JSON.stringify(message));
  }
  return total;
}

export function estimateToolsTokens(tools: readonly LLMToolDef[]): number {
  return estimateTextTokens(JSON.stringify(tools));
}

export function splitHistoryByRecentTurns(
  messages: readonly LLMMessage[],
  recentTurnCount = 3,
): HistorySplit {
  if (recentTurnCount <= 0 || messages.length === 0) {
    return { old: [...messages], recent: [] };
  }

  let seenUserTurns = 0;
  let recentStart = 0;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i]?.role !== "user") continue;
    seenUserTurns += 1;
    if (seenUserTurns === recentTurnCount) {
      recentStart = i;
      break;
    }
  }

  if (seenUserTurns < recentTurnCount) {
    return { old: [], recent: [...messages] };
  }
  return {
    old: messages.slice(0, recentStart),
    recent: messages.slice(recentStart),
  };
}

export class ContextChunkManager {
  allocate(chunks: readonly ContextChunk[], totalBudget: number): BudgetAllocation {
    const normalized = chunks
      .map((chunk, index) => ({
        chunk: { ...chunk },
        index,
      }))
      .sort((a, b) => {
        if (a.chunk.priority !== b.chunk.priority) {
          return a.chunk.priority - b.chunk.priority;
        }
        return a.index - b.index;
      })
      .map((entry) => entry.chunk);

    const mandatory = normalized.filter((chunk) => isMandatory(chunk));
    const optional = normalized.filter((chunk) => !isMandatory(chunk));
    const includedChunks = [...mandatory];
    const droppedChunks: DroppedContextChunk[] = [];
    const warnings: string[] = [];
    let usedTokens = sumTokens(includedChunks);

    if (usedTokens > totalBudget) {
      warnings.push(
        `mandatory context chunks exceed budget: mandatory=${usedTokens} budget=${totalBudget}`,
      );
      for (const chunk of optional) {
        droppedChunks.push(dropMetadata(chunk, "mandatory_overflow"));
      }
      return {
        totalBudget,
        usedTokens,
        overBudget: true,
        includedChunks,
        droppedChunks,
        warnings,
      };
    }

    includedChunks.push(...optional);
    usedTokens += sumTokens(optional);

    if (usedTokens > totalBudget) {
      const shrinkable = [...includedChunks]
        .filter((chunk) => !isMandatory(chunk) && chunk.shrinkable)
        .sort((a, b) => b.priority - a.priority);
      for (const chunk of shrinkable) {
        if (usedTokens <= totalBudget) break;
        const minTokens = Math.max(0, chunk.minTokens ?? 0);
        if (chunk.tokenCount <= minTokens) continue;
        const overage = usedTokens - totalBudget;
        const targetTokens = Math.max(minTokens, chunk.tokenCount - overage);
        const shrunk = shrinkChunk(chunk, targetTokens);
        usedTokens -= chunk.tokenCount - shrunk.tokenCount;
        Object.assign(chunk, shrunk);
      }
    }

    if (usedTokens > totalBudget) {
      for (let i = includedChunks.length - 1; i >= 0; i -= 1) {
        if (usedTokens <= totalBudget) break;
        const chunk = includedChunks[i];
        if (!chunk || isMandatory(chunk)) continue;
        includedChunks.splice(i, 1);
        usedTokens -= chunk.tokenCount;
        droppedChunks.push(dropMetadata(chunk, "budget_exceeded"));
      }
    }

    includedChunks.sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return normalized.findIndex((chunk) => chunk.id === a.id) -
        normalized.findIndex((chunk) => chunk.id === b.id);
    });

    return {
      totalBudget,
      usedTokens,
      overBudget: usedTokens > totalBudget,
      includedChunks,
      droppedChunks,
      warnings,
    };
  }

  systemChunks(system: string): ContextChunk[] {
    const extracted: Array<{
      category: ChunkCategory;
      id: string;
      content: string;
      firstIndex: number;
      shrinkable: boolean;
      minTokens?: number;
    }> = [];
    const ranges: Array<{ start: number; end: number }> = [];

    for (const spec of SYSTEM_BLOCK_SPECS) {
      const blocks: Array<{ content: string; index: number }> = [];
      for (const tag of spec.tags) {
        const re = new RegExp(`<${escapeRegExp(tag)}\\b[\\s\\S]*?<\\/${escapeRegExp(tag)}>`, "g");
        for (const match of system.matchAll(re)) {
          if (match.index === undefined) continue;
          blocks.push({ content: match[0], index: match.index });
          ranges.push({ start: match.index, end: match.index + match[0].length });
        }
      }
      blocks.sort((a, b) => a.index - b.index);
      if (blocks.length === 0) continue;
      extracted.push({
        category: spec.category,
        id: spec.id,
        content: blocks.map((block) => block.content).join("\n\n"),
        firstIndex: blocks[0]?.index ?? 0,
        shrinkable: spec.shrinkable,
        ...(spec.minTokens !== undefined ? { minTokens: spec.minTokens } : {}),
      });
    }

    const base = removeRanges(system, ranges).trim();
    if (base.length > 0) {
      extracted.push({
        category: "system",
        id: "system_prompt",
        content: base,
        firstIndex: system.indexOf(base.slice(0, Math.min(base.length, 32))),
        shrinkable: false,
      });
    }

    extracted.sort((a, b) => a.firstIndex - b.firstIndex);
    return extracted.map((entry) => ({
      id: entry.id,
      priority: CATEGORY_PRIORITIES[entry.category],
      category: entry.category,
      content: entry.content,
      tokenCount: estimateTextTokens(entry.content),
      shrinkable: entry.shrinkable,
      ...(entry.minTokens !== undefined ? { minTokens: entry.minTokens } : {}),
    }));
  }

  allocateSystem(
    system: string,
    tools: readonly LLMToolDef[],
    totalBudget: number,
  ): { system: string; allocation: BudgetAllocation } {
    const chunks = this.systemChunks(system);
    chunks.push({
      id: "tools",
      priority: CATEGORY_PRIORITIES.tools,
      category: "tools",
      content: JSON.stringify(tools),
      tokenCount: estimateToolsTokens(tools),
      shrinkable: false,
    });
    const allocation = this.allocate(chunks, totalBudget);
    const nextSystem = allocation.includedChunks
      .filter((chunk) => chunk.category !== "tools")
      .map((chunk) => chunk.content)
      .join("\n\n");
    return { system: nextSystem, allocation };
  }

  allocateHistoryMessages(
    historyMessages: readonly LLMMessage[],
    totalBudget: number,
    recentTurnCount = 3,
  ): AllocatedHistory {
    const split = splitHistoryByRecentTurns(historyMessages, recentTurnCount);
    const oldContent = JSON.stringify(split.old);
    const recentContent = JSON.stringify(split.recent);
    const chunks: ContextChunk[] = [];
    const oldTokenCount = estimateTextTokens(oldContent);
    const recentTokenCount = estimateTextTokens(recentContent);
    if (split.old.length > 0) {
      chunks.push({
        id: "history_old",
        priority: CATEGORY_PRIORITIES.history_old,
        category: "history_old",
        content: oldContent,
        tokenCount: oldTokenCount,
        shrinkable: true,
        minTokens: 512,
      });
    }
    if (split.recent.length > 0) {
      chunks.push({
        id: "history_recent",
        priority: CATEGORY_PRIORITIES.history_recent,
        category: "history_recent",
        content: recentContent,
        tokenCount: recentTokenCount,
        shrinkable: false,
      });
    }

    const allocation = this.allocate(chunks, totalBudget);
    const included = new Set(allocation.includedChunks.map((chunk) => chunk.id));
    const oldChunk = allocation.includedChunks.find((chunk) => chunk.id === "history_old");
    const oldMessages =
      oldChunk && oldChunk.tokenCount >= oldTokenCount
        ? split.old
        : oldChunk
          ? [truncatedOldHistoryMessage(oldTokenCount, oldChunk.tokenCount)]
          : [];
    const messages = [
      ...oldMessages,
      ...(included.has("history_recent") ? split.recent : []),
    ];
    return {
      messages,
      allocation,
      oldTokenCount,
      recentTokenCount,
    };
  }
}

function truncatedOldHistoryMessage(
  originalTokens: number,
  retainedTokens: number,
): LLMMessage {
  return {
    role: "user",
    content: [
      "<history_old_truncated hidden=\"true\">",
      `Original older history estimate: ${originalTokens} tokens.`,
      `Retained priority budget estimate: ${retainedTokens} tokens.`,
      "Older turns were truncated predictably because higher-priority context needed the window.",
      "Use any proactive history summary if one is injected on a later turn.",
      "</history_old_truncated>",
    ].join("\n"),
  };
}

function isMandatory(chunk: ContextChunk): boolean {
  return (
    chunk.priority <= CATEGORY_PRIORITIES.tools ||
    chunk.category === "system" ||
    chunk.category === "tools"
  );
}

function sumTokens(chunks: readonly ContextChunk[]): number {
  return chunks.reduce((sum, chunk) => sum + chunk.tokenCount, 0);
}

function dropMetadata(
  chunk: ContextChunk,
  reason: DroppedContextChunk["reason"],
): DroppedContextChunk {
  return {
    id: chunk.id,
    priority: chunk.priority,
    category: chunk.category,
    tokenCount: chunk.tokenCount,
    reason,
  };
}

function shrinkChunk(chunk: ContextChunk, targetTokens: number): ContextChunk {
  const boundedTarget = Math.max(0, Math.floor(targetTokens));
  if (chunk.tokenCount <= boundedTarget) return { ...chunk };
  if (boundedTarget === 0) {
    return { ...chunk, content: "", tokenCount: 0 };
  }

  const markerTokens = estimateTextTokens(TRUNCATION_MARKER);
  const bodyTokens = Math.max(1, boundedTarget - markerTokens);
  const maxChars = bodyTokens * CHARS_PER_TOKEN;
  const body =
    chunk.content.length > maxChars
      ? chunk.content.slice(0, maxChars)
      : chunk.content;
  return {
    ...chunk,
    content: `${body}${TRUNCATION_MARKER}`,
    tokenCount: boundedTarget,
  };
}

function removeRanges(input: string, ranges: readonly { start: number; end: number }[]): string {
  if (ranges.length === 0) return input;
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  let cursor = 0;
  let out = "";
  for (const range of sorted) {
    if (range.start < cursor) continue;
    out += input.slice(cursor, range.start);
    cursor = range.end;
  }
  out += input.slice(cursor);
  return out.replace(/\n{3,}/g, "\n\n");
}

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
