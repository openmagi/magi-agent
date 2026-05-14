import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { MissionClient } from "../missions/MissionClient.js";
import type { MissionChannelType, MissionKind } from "../missions/types.js";
import { errorResult } from "../util/toolResult.js";

export interface MissionLedgerInput {
  action: "create" | "heartbeat" | "block" | "complete" | "fail";
  missionId?: string;
  title?: string;
  kind?: MissionKind;
  message?: string;
  metadata?: Record<string, unknown>;
}

const MISSION_KINDS: MissionKind[] = [
  "manual",
  "goal",
  "spawn",
  "cron",
  "script_cron",
  "pipeline",
  "browser_qa",
  "document",
  "research",
];

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["create", "heartbeat", "block", "complete", "fail"] },
    missionId: { type: "string" },
    title: { type: "string" },
    kind: { type: "string", enum: MISSION_KINDS },
    message: { type: "string" },
    metadata: { type: "object", additionalProperties: true },
  },
  required: ["action"],
  additionalProperties: false,
} as const;

function eventTypeForAction(
  action: Exclude<MissionLedgerInput["action"], "create">,
): "heartbeat" | "blocked" | "completed" | "failed" {
  if (action === "heartbeat") return "heartbeat";
  if (action === "block") return "blocked";
  if (action === "complete") return "completed";
  return "failed";
}

export function makeMissionLedgerTool(deps: {
  client: MissionClient;
  getSourceChannel: (ctx: ToolContext) => { type: string; channelId: string } | null;
}): Tool<MissionLedgerInput, Record<string, unknown>> {
  return {
    name: "MissionLedger",
    description:
      "Create and update durable Missions for long-running work. Use for background missions, goals, blocked work, evidence, and final completion state.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    async execute(
      input,
      ctx,
    ): Promise<ToolResult<Record<string, unknown>>> {
      const start = Date.now();
      try {
        if (input.action === "create") {
          const channel = deps.getSourceChannel(ctx);
          if (!channel) {
            return {
              status: "error",
              errorCode: "no_channel",
              errorMessage: "Mission creation requires a source channel",
              durationMs: Date.now() - start,
            };
          }
          if (!input.title || !input.kind) {
            return {
              status: "error",
              errorCode: "invalid_input",
              errorMessage: "title and kind are required for create",
              durationMs: Date.now() - start,
            };
          }
          const mission = await deps.client.createMission({
            channelType: channel.type as MissionChannelType,
            channelId: channel.channelId,
            kind: input.kind,
            title: input.title,
            createdBy: "agent",
            metadata: input.metadata ?? {},
          });
          ctx.emitAgentEvent?.({ type: "mission_created", mission });
          return {
            status: "ok",
            output: { mission },
            durationMs: Date.now() - start,
          };
        }

        if (!input.missionId) {
          return {
            status: "error",
            errorCode: "mission_required",
            errorMessage: "missionId is required",
            durationMs: Date.now() - start,
          };
        }

        const eventType = eventTypeForAction(input.action);
        const event = await deps.client.appendEvent(input.missionId, {
          actorType: "agent",
          eventType,
          message: input.message,
          payload: input.metadata ?? {},
        });
        ctx.emitAgentEvent?.({
          type: "mission_event",
          missionId: input.missionId,
          eventType,
          message: input.message,
        });
        return {
          status: "ok",
          output: { event },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
