/**
 * Built-in hipocampus checkpoint hook — end-of-task memory feed.
 *
 * Kevin's hipocampus memory protocol (see project CLAUDE.md) persists
 * structured daily logs under `workspace/memory/YYYY-MM-DD.md` that
 * the compaction tree (Daily → Weekly → Monthly → Root) rolls up. This
 * hook is the runtime's native feed into that system.
 *
 * Design notes:
 * - Observer only — never blocks the turn.
 * - Only persists "interesting" turns (≥ 1 tool call OR assistantText
 *   ≥ 400 chars) to keep the daily log readable. Pure greetings are
 *   skipped.
 * - Best-effort Haiku summarisation when assistantText is long;
 *   failure falls back to raw truncated text so memory still lands.
 * - Atomic append — single `fs.appendFile`, no staging needed.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook, HookContext } from "../types.js";
import { isLongTermMemoryWriteDisabled } from "../../util/memoryMode.js";

const MIN_TEXT_LEN = 400;
const SUMMARISE_ABOVE = 1_200;
const MAX_SNIPPET = 1_200;

function fmtDate(d: Date): string {
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n).trimEnd() + "…";
}

async function summariseViaHaiku(
  ctx: HookContext,
  userMessage: string,
  assistantText: string,
): Promise<string | null> {
  if (assistantText.length < SUMMARISE_ABOVE) return null;
  const system =
    "Summarise this assistant turn for a daily memory log. 2-3 sentences. " +
    "Capture: what the user asked, what the assistant concluded, any artifacts or decisions. " +
    "Respond in the language of the user message. No preamble, no quotes.";
  const prompt = `User: ${truncate(userMessage, 400)}\n\nAssistant: ${truncate(assistantText, 4_000)}`;
  try {
    let out = "";
    const deadline = Date.now() + 4_000;
    for await (const evt of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system,
      messages: [{ role: "user", content: prompt }],
      max_tokens: 200,
      temperature: 0,
    })) {
      if (Date.now() > deadline) break;
      if (evt.kind === "text_delta") out += evt.delta;
      if (evt.kind === "message_end" || evt.kind === "error") break;
    }
    out = out.trim();
    return out.length > 0 ? out : null;
  } catch {
    return null;
  }
}

export function makeHipocampusCheckpointHook(workspaceRoot: string): RegisteredHook<"onTaskCheckpoint"> {
  return {
    name: "builtin:hipocampus-checkpoint",
    point: "onTaskCheckpoint",
    priority: 100,
    blocking: false, // pure observer
    timeoutMs: 8_000, // summariser can take a few seconds
    handler: async (args, ctx: HookContext) => {
      if (isLongTermMemoryWriteDisabled(ctx.memoryMode)) return;
      // Skip trivial turns (no tools, short text).
      if (args.toolCallCount === 0 && args.assistantText.length < MIN_TEXT_LEN) {
        return;
      }

      const dateStr = fmtDate(new Date(args.endedAt));
      const memoryDir = path.join(workspaceRoot, "memory");
      const logPath = path.join(memoryDir, `${dateStr}.md`);

      let summary: string | null = null;
      try {
        summary = await summariseViaHaiku(ctx, args.userMessage, args.assistantText);
      } catch {
        summary = null;
      }

      const toolLine =
        args.toolCallCount > 0
          ? `**Tools:** ${args.toolCallCount} call${args.toolCallCount === 1 ? "" : "s"} — ${[...new Set(args.toolNames)].join(", ")}\n`
          : "";
      const filesLine =
        args.filesChanged.length > 0
          ? `**Files changed:** ${args.filesChanged.map((p) => `\`${p}\``).join(", ")}\n`
          : "";
      const durationSec = Math.max(0, Math.round((args.endedAt - args.startedAt) / 1000));

      const entry = [
        `\n## ${new Date(args.endedAt).toISOString()} · ${ctx.turnId} · ${durationSec}s`,
        "",
        `**User:** ${truncate(args.userMessage.replace(/\s+/g, " "), 300)}`,
        "",
        summary
          ? `**Summary:** ${summary}`
          : `**Assistant:** ${truncate(args.assistantText.replace(/\s+/g, " "), MAX_SNIPPET)}`,
        "",
        toolLine,
        filesLine,
        "---",
        "",
      ]
        .filter(Boolean)
        .join("\n");

      try {
        await fs.mkdir(memoryDir, { recursive: true });
        await fs.appendFile(logPath, entry, "utf8");
        ctx.log("info", "hipocampus checkpoint appended", {
          file: logPath,
          bytes: Buffer.byteLength(entry, "utf8"),
          summarised: summary !== null,
        });
      } catch (err) {
        ctx.log("warn", "hipocampus checkpoint write failed", {
          error: String(err),
        });
      }
    },
  };
}
