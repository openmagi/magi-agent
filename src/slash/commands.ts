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

export function makeCompactCommand(agent: Agent): SlashCommand {
  return {
    name: "/compact",
    aliases: ["/compress"],
    description:
      "Force an immediate compaction of the current session transcript + memory tree.",
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const { session, sse } = ctx;
      const transcriptEntries = await session.transcript.readAll();

      // 1. Transcript compaction (existing)
      try {
        await agent.contextEngine.maybeCompact(
          session,
          transcriptEntries,
          /*tokenLimit=*/ 0,
          agent.config.model,
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        emitText(sse, `⚠️ Transcript compaction failed: ${msg}`);
      }

      // 2. Hipocampus memory compaction (forced, no cooldown)
      if (agent.hipocampus) {
        try {
          const result = await agent.hipocampus.compact(true);
          if (result.compacted) {
            emitText(
              sse,
              `✅ Compaction complete — transcript + memory tree (daily=${result.stats.daily.length} weekly=${result.stats.weekly.length} monthly=${result.stats.monthly.length}).`,
            );
          } else {
            emitText(sse, "✅ Transcript compacted. Memory tree: nothing to compact.");
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          emitText(sse, `✅ Transcript compacted. ⚠️ Memory tree failed: ${msg}`);
        }
      } else {
        emitText(sse, "✅ Compaction complete. Summary boundary written.");
      }
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

      // Flush memory before reset so no context is lost
      try {
        const transcript = await session.transcript.readAll();
        await flushMemory(agent.config.workspaceRoot, transcript);
      } catch {
        // flush failure is non-fatal
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
    `- Model: ${agent.config.model}`,
  ];
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
      emitText(sse, formatStatusText(agent, session, counter));
    },
  };
}

// ── Registration helper ───────────────────────────────────────────────

export { formatStatusText };
