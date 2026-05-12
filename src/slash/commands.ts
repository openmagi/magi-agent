/**
 * Built-in slash commands: `/compact`, `/reset`, `/status`.
 *
 * Each command emits a single `text_delta` on the `event: agent` SSE
 * channel. None of them runs the Turn LLM path — the response is
 * synthetic.
 */

import type { Agent } from "../Agent.js";
import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { SlashCommand, SlashCommandContext } from "./registry.js";
import { ResetCounterStore } from "./resetCounters.js";
import { flushMemory } from "../hooks/builtin/hipocampusFlush.js";
import { isLongTermMemoryWriteDisabled } from "../util/memoryMode.js";

/**
 * Write a synthetic assistant text response onto the `event: agent`
 * SSE channel. The legacy OpenAI-compat `choices[0].delta.content`
 * path was previously dual-emitted but caused every token to render
 * twice once the web client wired `text_delta` — see LLMStreamReader.ts
 * for the full regression note (commit eda9047c, 2026-04-20).
 */
function emitText(sse: SseWriter, text: string): void {
  sse.agent({ type: "text_delta", delta: text });
}

// ── /compact ──────────────────────────────────────────────────────────

function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}

function emitCompactSummary(
  sse: SseWriter,
  transcriptBefore: number,
  transcriptAfter: number,
  memoryStats: { daily: string[]; weekly: string[]; monthly: string[] } | null,
  memoryError?: string,
): void {
  const lines: string[] = ["✅ Compaction complete\n"];
  if (transcriptBefore > 0) {
    const saved = transcriptBefore - transcriptAfter;
    const pct = Math.round((saved / transcriptBefore) * 100);
    lines.push(`📝 Transcript: ${fmtNum(transcriptBefore)} → ${fmtNum(transcriptAfter)} tokens (${pct}% reduced)`);
  } else {
    lines.push("📝 Transcript: already compact");
  }
  if (memoryError) {
    lines.push(`🧠 Memory tree: ⚠️ ${memoryError}`);
  } else if (memoryStats) {
    const parts: string[] = [];
    if (memoryStats.daily.length) parts.push(`${memoryStats.daily.length} daily`);
    if (memoryStats.weekly.length) parts.push(`${memoryStats.weekly.length} weekly`);
    if (memoryStats.monthly.length) parts.push(`${memoryStats.monthly.length} monthly`);
    lines.push(`🧠 Memory tree: ${parts.length ? parts.join(", ") + " compacted" : "nothing to compact"}`);
  }
  emitText(sse, lines.join("\n"));
}

export function makeCompactCommand(agent: Agent): SlashCommand {
  return {
    name: "/compact",
    aliases: ["/compress"],
    description:
      "Force an immediate compaction of the current session transcript + memory tree.",
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const { session, sse } = ctx;
      sse.agent({ type: "turn_phase", turnId: "compact", phase: "compacting" });
      const transcriptEntries = await session.transcript.readAll();
      let transcriptBefore = 0;
      let transcriptAfter = 0;

      sse.agent({ type: "tool_start", id: "compact-transcript", name: "Transcript Compaction" });
      const t0 = Date.now();
      try {
        const boundary = await agent.contextEngine.maybeCompact(
          session,
          transcriptEntries,
          /*tokenLimit=*/ 0,
          agent.config.model,
        );
        if (boundary) {
          transcriptBefore = boundary.beforeTokenCount;
          transcriptAfter = boundary.afterTokenCount;
          sse.agent({
            type: "tool_end",
            id: "compact-transcript",
            status: "done",
            output_preview: `${fmtNum(transcriptBefore)} → ${fmtNum(transcriptAfter)} tokens`,
            durationMs: Date.now() - t0,
          });
        } else {
          sse.agent({
            type: "tool_end",
            id: "compact-transcript",
            status: "done",
            output_preview: "Already compact",
            durationMs: Date.now() - t0,
          });
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        sse.agent({
          type: "tool_end",
          id: "compact-transcript",
          status: "error",
          output_preview: msg,
          durationMs: Date.now() - t0,
        });
      }

      if (agent.hipocampus) {
        sse.agent({ type: "tool_start", id: "compact-memory", name: "Memory Tree Compaction" });
        const t1 = Date.now();
        try {
          const result = await agent.hipocampus.compact(true);
          if (result.compacted) {
            const parts: string[] = [];
            if (result.stats.daily.length) parts.push(`daily ${result.stats.daily.length}`);
            if (result.stats.weekly.length) parts.push(`weekly ${result.stats.weekly.length}`);
            if (result.stats.monthly.length) parts.push(`monthly ${result.stats.monthly.length}`);
            sse.agent({
              type: "tool_end",
              id: "compact-memory",
              status: "done",
              output_preview: parts.join(", ") || "No changes",
              durationMs: Date.now() - t1,
            });
            emitCompactSummary(sse, transcriptBefore, transcriptAfter, result.stats);
          } else {
            sse.agent({
              type: "tool_end",
              id: "compact-memory",
              status: "done",
              output_preview: "Nothing to compact",
              durationMs: Date.now() - t1,
            });
            emitCompactSummary(sse, transcriptBefore, transcriptAfter, null);
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          sse.agent({
            type: "tool_end",
            id: "compact-memory",
            status: "error",
            output_preview: msg,
            durationMs: Date.now() - t1,
          });
          emitCompactSummary(sse, transcriptBefore, transcriptAfter, null, msg);
        }
      } else {
        emitCompactSummary(sse, transcriptBefore, transcriptAfter, null);
      }
      sse.agent({ type: "turn_phase", turnId: "compact", phase: "committed" });
    },
  };
}

// ── /reset ────────────────────────────────────────────────────────────

export function makeResetCommand(
  agent: Agent,
  resetStore: ResetCounterStore,
): SlashCommand {
  return {
    name: "/reset",
    description:
      "Start a fresh conversation — next message creates a new session for this channel.",
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const { session, sse } = ctx;
      const ref = session.meta.channel;

      // Flush memory before reset so no context is lost, except channels
      // with disabled long-term memory writes.
      if (!isLongTermMemoryWriteDisabled(ref.memoryMode)) {
        try {
          const transcript = await session.transcript.readAll();
          await flushMemory(agent.config.workspaceRoot, transcript);
        } catch {
          // flush failure is non-fatal
        }
      }

      const next = await resetStore.bump(ref);
      // Audit — so operators can trace who triggered a reset.
      // AuditLog.append is already best-effort (swallows write errors
      // internally), so no outer try/catch needed.
      await agent.auditLog.append(
        "slash_command",
        session.meta.sessionKey,
        undefined,
        {
          command: "/reset",
          counter: next,
          channelType: ref.type,
          channelId: ref.channelId,
        },
      );
      emitText(sse, "✅ Conversation reset. New session starting.");
    },
  };
}

// ── /status ───────────────────────────────────────────────────────────

function formatSessionRole(session: Session): string {
  return session.meta.role === "subagent" ? "subagent" : "main";
}

function formatSkills(agent: Agent): { count: number; names: string[] } {
  const skillTools = agent.tools.list().filter((t) => t.kind === "skill");
  return {
    count: skillTools.length,
    names: skillTools.map((t) => t.name),
  };
}

function formatStatusText(
  agent: Agent,
  session: Session,
  resetCounter: number,
  runtimeModelOverride?: string,
): string {
  const budget = session.budgetStats();
  const skills = formatSkills(agent);
  const cronCount = agent.crons.list().length;
  const discipline = session.meta.discipline;
  const disciplineLine = discipline
    ? `tdd=${discipline.tdd} git=${discipline.git} enforcement=${discipline.requireCommit}`
    : "off";
  const activeContext = session.getActiveContext();
  const skillList =
    skills.count === 0
      ? "(none)"
      : skills.names.slice(0, 20).join(", ") +
        (skills.count > 20 ? `, … (+${skills.count - 20} more)` : "");

  const lines = [
    "📊 Session status",
    `- Role: ${formatSessionRole(session)}`,
    `- Channel: ${session.meta.channel.type}:${session.meta.channel.channelId}`,
    `- Reset counter: ${resetCounter}`,
    `- Context: ${activeContext.meta.contextId} (${activeContext.meta.title})`,
    `- Turns (this session): ${budget.turns}`,
    `- Tokens — input: ${budget.inputTokens}, output: ${budget.outputTokens}`,
    `- Cost (USD): ${budget.costUsd.toFixed(4)}`,
    `- Skills loaded: ${skills.count} — ${skillList}`,
    `- Active crons: ${cronCount}`,
    `- Discipline: ${disciplineLine}`,
  ];
  const runtimeModel = runtimeModelOverride?.trim();
  if (runtimeModel) lines.push(`- Model: ${runtimeModel}`);
  return lines.join("\n");
}

export function makeStatusCommand(
  agent: Agent,
  resetStore: ResetCounterStore,
): SlashCommand {
  return {
    name: "/status",
    description:
      "Print current session meta — role, channel, reset counter, usage, skills, crons.",
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const { session, sse } = ctx;
      const counter = await resetStore.get(session.meta.channel);
      emitText(sse, formatStatusText(agent, session, counter, ctx.runtimeModelOverride));
    },
  };
}

// ── Registration helper ───────────────────────────────────────────────

export { formatStatusText };
