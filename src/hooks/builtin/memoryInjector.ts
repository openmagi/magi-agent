/**
 * Built-in memory-injector hook — T1-01 (Phase 3).
 *
 * Design reference:
 * - `docs/plans/2026-04-19-core-agent-phase-3-plan.md` §3 / T1-01
 * - `docs/plans/2026-04-19-magi-core-agent-design.md` §7.12.c
 *   (memory fencing format)
 *
 * On the first iteration of each user turn, this hook queries qmd
 * (BM25 search over `workspace/memory/`) with the latest user message
 * as the query. Top results are packed into a fenced system
 * attachment following the §7.12.c shape:
 *
 *   <memory-context source="qmd" tier="L0">
 *   [path: memory/2026-04-19.md]
 *   ...
 *   </memory-context>
 *
 * The block is prepended to the existing `system` string so existing
 * hooks that run later still see their augmented content.
 *
 * Fail-open: qmd unreachable / timeout / HTTP error => log a warning
 * and return `{ action: "continue" }` without modification. Memory is
 * "nice to have" — it must never block a turn.
 *
 * Toggle:
 * - `CORE_AGENT_MEMORY_INJECTION=off` (env) disables the hook.
 * - `workspace/agent.config.yaml: memory_injection: off` (file) also
 *   disables; file override beats env so operators can flip it per-bot.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import type { HipocampusService, RootMemory } from "../../services/memory/HipocampusService.js";
import {
  classifyMemoryContinuity,
  extractDistinctivePhrases,
  type MemoryContinuity,
  type MemoryRecallRecord,
} from "../../reliability/MemoryContinuity.js";

/** Soft budget for total injected content (bytes, UTF-8). */
const MAX_BYTES_INJECTED = 5_000;
/** qmd HTTP call budget. Raised from 2s to 5s to accommodate hybrid
 *  (BM25 + vector) parallel search on Max/Flex bots. */
const QMD_TIMEOUT_MS = 5_000;
/** Default qmd collection for memory files. */
const DEFAULT_COLLECTION = "memory";
/** Default qmd search limit. */
const DEFAULT_LIMIT = 5;
/** Default qmd minimum score. */
const DEFAULT_MIN_SCORE = 0.3;
const MAX_ROOT_BYTES = 1_500;
const MEMORY_CONTINUITY_POLICY = [
  '<memory-continuity-policy hidden="true">',
  "Recalled memory is reference material, not conversation state.",
  "The latest user message owns the current task.",
  "Memory marked background must not introduce an old pending question, decision, or task unless the latest user message explicitly asks to continue that topic.",
  "Memory marked related may inform the answer, but do not let it change what the user asked for.",
  "</memory-continuity-policy>",
].join("\n");

interface QmdResult {
  path: string;
  content: string;
  score: number;
  context?: string;
  continuity?: MemoryContinuity;
}

interface QmdSearchResponse {
  results: QmdResult[];
}

function isEnabledByEnv(): boolean {
  const raw = process.env.CORE_AGENT_MEMORY_INJECTION;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  if (v === "" || v === "on" || v === "true" || v === "1") return true;
  return false;
}

function getQmdUrl(): string | null {
  const raw = process.env.QMD_URL?.trim();
  if (!raw) return null;
  return raw.replace(/\/+$/, "");
}

function getCollection(): string {
  const raw = process.env.CORE_AGENT_MEMORY_INJECT_COLLECTION?.trim();
  return raw && raw.length > 0 ? raw : DEFAULT_COLLECTION;
}

function getLimit(): number {
  const raw = process.env.CORE_AGENT_MEMORY_INJECT_LIMIT;
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_LIMIT;
}

function getMinScore(): number {
  const raw = process.env.CORE_AGENT_MEMORY_INJECT_MIN_SCORE;
  const n = raw ? Number.parseFloat(raw) : NaN;
  return Number.isFinite(n) && n >= 0 && n <= 1 ? n : DEFAULT_MIN_SCORE;
}

/**
 * Read `workspace/agent.config.yaml` and check for `memory_injection`.
 * Returns `true` if the file is missing or the key is absent (default
 * on). Returns `false` only if the key is explicitly "off" | "false" |
 * "0" | "disabled".
 */
async function isEnabledByWorkspaceConfig(workspaceRoot: string | undefined): Promise<boolean> {
  if (!workspaceRoot) return true;
  const configPath = path.join(workspaceRoot, "agent.config.yaml");
  let raw: string;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return true;
    return true; // unreadable config -> default on
  }
  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch {
    return true;
  }
  if (!parsed || typeof parsed !== "object") return true;
  const val = (parsed as Record<string, unknown>)["memory_injection"];
  if (val === undefined || val === null) return true;
  if (typeof val === "boolean") return val;
  if (typeof val === "string") {
    const v = val.trim().toLowerCase();
    if (v === "off" || v === "false" || v === "0" || v === "disabled" || v === "no") {
      return false;
    }
  }
  return true;
}

function extractLastUserText(messages: LLMMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.role !== "user") continue;
    if (typeof m.content === "string") return m.content;
    if (Array.isArray(m.content)) {
      const parts: string[] = [];
      for (const b of m.content) {
        if (b && typeof b === "object" && "type" in b && b.type === "text" && "text" in b) {
          parts.push((b as { text: string }).text);
        }
      }
      return parts.join("\n");
    }
  }
  return "";
}

/**
 * True when the LAST message is a user message (i.e. no assistant
 * response in between). On follow-up iterations of a multi-iteration
 * turn the model has already emitted an assistant block so the last
 * message in the constructed prompt is an assistant message with
 * tool_use.
 */
function isFirstIteration(messages: LLMMessage[]): boolean {
  if (messages.length === 0) return false;
  const last = messages[messages.length - 1];
  return !!last && last.role === "user";
}

export interface QmdSearchParams {
  query: string;
  collection: string;
  limit: number;
  minScore: number;
}

/** Exported for tests — performs one POST /search call with a
 * bounded timeout. Returns null on any failure (caller fails open). */
export async function searchQmd(
  qmdUrl: string,
  params: QmdSearchParams,
  timeoutMs: number = QMD_TIMEOUT_MS,
  fetchImpl: typeof fetch = fetch,
): Promise<QmdResult[] | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${qmdUrl}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: params.query,
        collection: params.collection,
        limit: params.limit,
        minScore: params.minScore,
      }),
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const json = (await res.json()) as QmdSearchResponse;
    if (!json || !Array.isArray(json.results)) return null;
    return json.results;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Build the `<memory-context>` fenced block from qmd results. Caps
 * total byte count so a memory-heavy hit set can't blow out the
 * system prompt.
 */
export function buildMemoryFence(
  results: QmdResult[],
  maxBytes: number = MAX_BYTES_INJECTED,
): { fence: string; bytes: number; used: number } {
  const header = `<memory-context source="qmd" tier="L0">`;
  const footer = `</memory-context>`;
  const overhead = Buffer.byteLength(header, "utf8") + Buffer.byteLength(footer, "utf8") + 2;
  let remaining = Math.max(0, maxBytes - overhead);
  const parts: string[] = [];
  let used = 0;
  for (const r of results) {
    if (!r || typeof r.path !== "string" || typeof r.content !== "string") continue;
    const label = r.continuity ? `\n[continuity: ${r.continuity}]` : "";
    const block = `[path: ${r.path}]${label}\n${r.content.trim()}`;
    const blockBytes = Buffer.byteLength(block, "utf8") + 2; // + "\n\n"
    if (blockBytes > remaining) {
      // Try truncating to fit remaining budget — but only if we can
      // still fit the header + some content meaningfully.
      const minBlock = `[path: ${r.path}]${label}\n…`;
      const minBytes = Buffer.byteLength(minBlock, "utf8") + 2;
      if (remaining < minBytes) break;
      const prefix = `[path: ${r.path}]${label}\n`;
      const prefixBytes = Buffer.byteLength(prefix, "utf8");
      const room = remaining - prefixBytes - 2 - 1; // -1 for ellipsis
      if (room <= 0) break;
      const truncated = sliceUtf8Bytes(r.content.trim(), room);
      parts.push(`${prefix}${truncated}…`);
      used++;
      remaining = 0;
      break;
    }
    parts.push(block);
    used++;
    remaining -= blockBytes;
  }
  if (parts.length === 0) {
    return { fence: "", bytes: 0, used: 0 };
  }
  const body = parts.join("\n\n");
  const fence = `${header}\n${body}\n${footer}`;
  return { fence, bytes: Buffer.byteLength(fence, "utf8"), used };
}

export function buildRootMemoryFence(
  root: RootMemory,
  continuity: MemoryContinuity = "background",
  maxBytes: number = MAX_ROOT_BYTES,
): { fence: string; bytes: number } {
  const header = `<memory-root source="hipocampus-root" tier="L2" continuity="${continuity}">`;
  const footer = `</memory-root>`;
  const prefix = `[path: ${root.path}]\n`;
  const overhead =
    Buffer.byteLength(header, "utf8") +
    Buffer.byteLength(footer, "utf8") +
    Buffer.byteLength(prefix, "utf8") +
    3;
  const remaining = Math.max(0, maxBytes - overhead);
  const content =
    Buffer.byteLength(root.content, "utf8") <= remaining
      ? root.content
      : `${sliceUtf8Bytes(root.content, Math.max(0, remaining - 1))}…`;
  const fence = `${header}\n${prefix}${content}\n${footer}`;
  return { fence, bytes: Buffer.byteLength(fence, "utf8") };
}

function memoryRecordForQmdResult(
  result: QmdResult,
  userText: string,
  turnId: string,
): MemoryRecallRecord {
  return {
    turnId,
    source: "qmd",
    path: result.path,
    continuity: classifyMemoryContinuity({
      latestUserText: userText,
      memoryText: result.content,
      source: "qmd",
    }),
    distinctivePhrases: extractDistinctivePhrases(result.content),
  };
}

function memoryRecordForRoot(
  root: RootMemory,
  userText: string,
  turnId: string,
): MemoryRecallRecord {
  return {
    turnId,
    source: "root",
    path: root.path,
    continuity: classifyMemoryContinuity({
      latestUserText: userText,
      memoryText: root.content,
      source: "root",
    }),
    distinctivePhrases: extractDistinctivePhrases(root.content),
  };
}

/** Slice `s` to at most `maxBytes` UTF-8 bytes without splitting a
 * multi-byte codepoint. */
function sliceUtf8Bytes(s: string, maxBytes: number): string {
  const buf = Buffer.from(s, "utf8");
  if (buf.length <= maxBytes) return s;
  let end = maxBytes;
  // Step back if we are in the middle of a UTF-8 continuation byte.
  while (end > 0 && ((buf[end] ?? 0) & 0xc0) === 0x80) end--;
  return buf.subarray(0, end).toString("utf8");
}

export interface MemoryInjectorOptions {
  workspaceRoot?: string;
  /** Native qmd manager — when provided, bypasses HTTP and calls qmd CLI directly. */
  qmdManager?: {
    isReady(): boolean;
    search(query: string, opts?: { collection?: string; limit?: number; minScore?: number }): Promise<QmdResult[]>;
    hybridSearch?(query: string, opts?: { collection?: string; limit?: number; minScore?: number }): Promise<QmdResult[]>;
  };
  hipocampus?: Pick<HipocampusService, "recall">;
}

export function makeMemoryInjectorHook(
  opts: MemoryInjectorOptions = {},
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:memory-injector",
    point: "beforeLLMCall",
    priority: 5,
    blocking: true,
    timeoutMs: QMD_TIMEOUT_MS + 500,
    handler: async ({ messages, tools, system, iteration }, ctx: HookContext) => {
      // Only inject on the first iteration of a turn — subsequent
      // iterations already carry the block in `system`.
      if (iteration > 0) return { action: "continue" };

      // Env toggle (global).
      if (!isEnabledByEnv()) return { action: "continue" };

      // Skip follow-up iterations where the last message is assistant.
      if (!isFirstIteration(messages)) return { action: "continue" };

      const userText = extractLastUserText(messages);
      if (!userText || userText.trim().length === 0) {
        return { action: "continue" };
      }

      // Workspace file override beats env default-on.
      const fileEnabled = await isEnabledByWorkspaceConfig(opts.workspaceRoot);
      if (!fileEnabled) return { action: "continue" };

      const collection = getCollection();
      const limit = getLimit();
      const minScore = getMinScore();
      const startedAt = Date.now();
      let root: RootMemory | null = null;

      // Try native QmdManager first (no HTTP), fall back to legacy HTTP.
      // Use hybridSearch (BM25 + vector) when available for better recall.
      let results: QmdResult[] | null = null;
      if (opts.hipocampus) {
        try {
          const recall = await opts.hipocampus.recall(userText.slice(0, 4_000), {
            collection,
            limit,
            minScore,
          });
          root = recall.root;
          results = recall.results;
        } catch {
          results = null;
          root = null;
        }
      } else if (opts.qmdManager?.isReady()) {
        try {
          const searchFn = opts.qmdManager.hybridSearch
            ? opts.qmdManager.hybridSearch.bind(opts.qmdManager)
            : opts.qmdManager.search.bind(opts.qmdManager);
          results = await searchFn(userText.slice(0, 4_000), {
            collection, limit, minScore,
          });
        } catch {
          results = null;
        }
      } else {
        const qmdUrl = getQmdUrl();
        if (!qmdUrl) {
          ctx.log("warn", "[memoryInjector] no qmd source (native not ready, QMD_URL not set)");
          return { action: "continue" };
        }
        results = await searchQmd(qmdUrl, {
          query: userText.slice(0, 4_000),
          collection,
          limit,
          minScore,
        });
      }

      const durationMs = Date.now() - startedAt;

      if (results === null) {
        ctx.log("warn", "[memoryInjector] failed: qmd unreachable or error", {
          collection,
          durationMs,
        });
        return { action: "continue" };
      }

      if (results.length === 0 && !root) {
        ctx.log("info", "[memoryInjector] no matches", {
          collection,
          durationMs,
        });
        return { action: "continue" };
      }

      const fences: string[] = [];
      let bytes = 0;
      let used = 0;
      const records: MemoryRecallRecord[] = [];
      if (root) {
        const rootRecord = memoryRecordForRoot(root, userText, ctx.turnId);
        records.push(rootRecord);
        const rootFence = buildRootMemoryFence(root, rootRecord.continuity);
        if (rootFence.fence.length > 0) {
          fences.push(rootFence.fence);
          bytes += rootFence.bytes;
        }
      }
      if (results.length > 0) {
        const resultRecords = results.map((result) =>
          memoryRecordForQmdResult(result, userText, ctx.turnId),
        );
        const enrichedResults = results.map((result, index) => ({
          ...result,
          continuity: resultRecords[index]?.continuity ?? "background",
        }));
        const recallFence = buildMemoryFence(enrichedResults);
        if (recallFence.fence.length > 0 && recallFence.used > 0) {
          fences.push(recallFence.fence);
          bytes += recallFence.bytes;
          used += recallFence.used;
          records.push(...resultRecords.slice(0, recallFence.used));
        }
      }
      if (fences.length === 0) {
        return { action: "continue" };
      }
      if (records.length > 0) {
        ctx.executionContract?.replaceMemoryRecallForTurn(ctx.turnId, records);
      }
      const fence = [MEMORY_CONTINUITY_POLICY, ...fences].join("\n\n");

      // Audit trace — use rule_check emit (the hook-accessible audit
      // pathway per citationGate / answerVerifier pattern).
      ctx.emit({
        type: "rule_check",
        ruleId: "memory-injector",
        verdict: "ok",
        detail: `injected=${used} bytes=${bytes} durationMs=${durationMs}`,
      });

      ctx.log("info", "[memoryInjector] memory_injected", {
        query: userText.slice(0, 120),
        resultCount: used,
        rootIncluded: root !== null,
        bytesInjected: bytes,
        durationMs,
      });

      const nextSystem = system ? `${fence}\n\n${system}` : fence;
      return {
        action: "replace",
        value: { messages, tools, system: nextSystem, iteration },
      };
    },
  };
}
