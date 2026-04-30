/**
 * CronCreate — schedule a recurring bot prompt. Critical behavior:
 * the deliveryChannel is CAPTURED FROM THE TURN'S SOURCE CHANNEL at
 * creation time (passed in by ctx via ctx.metadata.sourceChannel, set
 * by Turn). The bot does NOT pick the target channel itself — this
 * fixes the legacy gateway bug where web-created crons routinely delivered
 * to Telegram because the LLM guessed wrong.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { CronScheduler, CronRecord } from "../cron/CronScheduler.js";
import type { ChannelRef } from "../util/types.js";
import type { Session } from "../Session.js";
import { errorResult } from "../util/toolResult.js";

export interface CronCreateInput {
  expression: string;
  prompt: string;
  description?: string;
  /**
   * Optional explicit channel override. When omitted, defaults to the
   * current turn's source channel (see Turn.ts metadata). This is the
   * preferred path — the bot should NOT pass this in most cases.
   */
  deliveryChannel?: ChannelRef;
  /**
   * If `true`, the cron survives session end and runs until explicitly
   * deleted. Defaults to `false` (session-scoped). See tool description
   * for validation rules.
   */
  durable?: boolean;
}

export interface CronCreateOutput {
  cron: CronRecord;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    expression: {
      type: "string",
      description:
        "5-field cron expression (min hour dom mon dow) OR shorthand (@hourly, @daily, @weekly, @monthly, @yearly).",
    },
    prompt: {
      type: "string",
      description: "The prompt the bot will receive when the cron fires.",
    },
    description: { type: "string", description: "Optional human-facing label." },
    deliveryChannel: {
      type: "object",
      description:
        "Optional explicit delivery channel. Omit to inherit the current turn's channel — the recommended behavior.",
      properties: {
        type: { type: "string", enum: ["app", "telegram", "discord"] },
        channelId: { type: "string" },
      },
      required: ["type", "channelId"],
    },
    durable: {
      type: "boolean",
      description:
        "Optional, default false. If true, this cron survives session end and runs until explicitly deleted — use for long-running scheduled tasks (daily reports, weekly digests). Default false creates a session-scoped cron that is dropped when the session ends. Rejected when the session is a subagent or lacks a delivery channel.",
    },
  },
  required: ["expression", "prompt"],
} as const;

export interface CronCreateDeps {
  scheduler: CronScheduler;
  botId: string;
  userId: string;
  /**
   * Returns the source channel of the current turn. Turn.ts populates
   * this via a closure so the tool doesn't need to know about Turn
   * internals.
   */
  getSourceChannel: (ctx: ToolContext) => ChannelRef | null;
  /**
   * Returns the owning Session for the current turn — used to enforce
   * durable-flag validation (subagent / missing channel rejection)
   * and to track session-scoped cron ids on `Session.meta.crons`.
   * Returns null when the turn's session is not resolvable (e.g.
   * child turns run in a stub session); in that case durable=true is
   * rejected and non-durable crons fall back to running in memory
   * without a session anchor.
   */
  getSession: (ctx: ToolContext) => Session | null;
}

export function makeCronCreateTool(
  deps: CronCreateDeps,
): Tool<CronCreateInput, CronCreateOutput> {
  return {
    name: "CronCreate",
    description:
      "Schedule a recurring prompt to fire at cron times. Delivery channel " +
      "is AUTOMATICALLY CAPTURED from the current turn — do NOT pass " +
      "deliveryChannel unless the user explicitly asked for a different channel. " +
      "durable (optional, default false): if true, this cron survives session " +
      "end and runs until explicitly deleted. Use for long-running scheduled " +
      "tasks (daily reports, weekly digests). Default false creates a " +
      "session-scoped cron that is dropped when the session ends.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    async execute(
      input: CronCreateInput,
      ctx: ToolContext,
    ): Promise<ToolResult<CronCreateOutput>> {
      const start = Date.now();
      try {
        const session = deps.getSession(ctx);
        const channel = input.deliveryChannel ?? deps.getSourceChannel(ctx);
        const durable = input.durable === true;

        // Validation path — all rejects happen BEFORE the scheduler
        // is mutated so the write fails atomically.
        if (durable) {
          if (session?.meta.role === "subagent") {
            return {
              status: "error",
              errorCode: "durable_subagent_rejected",
              errorMessage: "durable=true requires a non-subagent session",
              durationMs: Date.now() - start,
            };
          }
          if (!channel || !session || !session.meta.channel) {
            return {
              status: "error",
              errorCode: "durable_no_channel",
              errorMessage:
                "durable=true requires a session with an attached delivery channel (telegram/discord/app)",
              durationMs: Date.now() - start,
            };
          }
        }

        if (!channel) {
          return {
            status: "error",
            errorCode: "no_delivery_channel",
            errorMessage:
              "Could not infer a delivery channel and none was supplied.",
            durationMs: Date.now() - start,
          };
        }
        const cron = await deps.scheduler.create({
          botId: deps.botId,
          userId: deps.userId,
          expression: input.expression,
          prompt: input.prompt,
          deliveryChannel: channel,
          ...(input.description ? { description: input.description } : {}),
          durable,
          ...(!durable && session ? { sessionKey: session.meta.sessionKey } : {}),
        });
        // Session-scoped crons register on their owning Session so
        // Session.close() can sweep them. Durable crons do NOT touch
        // Session.meta.crons — they're anchored to the on-disk index.
        if (!durable && session) {
          session.registerSessionCron(cron.cronId);
        }
        return {
          status: "ok",
          output: { cron },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
