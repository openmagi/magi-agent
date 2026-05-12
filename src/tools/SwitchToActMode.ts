import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { ToolRegistry } from "./ToolRegistry.js";

export function makeSwitchToActModeTool(registry: ToolRegistry): Tool<Record<string, never>> {
  return {
    name: "SwitchToActMode",
    description:
      "Transition from plan mode to act mode, unlocking write and execute tools. " +
      "Call this after the plan is finalized and approved.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    permission: "meta",
    availableInModes: ["plan"],
    async execute(_input: Record<string, never>, ctx: ToolContext): Promise<ToolResult> {
      const previousMode = registry.getMode();
      registry.setMode("act");
      ctx.staging.stageAuditEvent("mode_transition", {
        from: previousMode,
        to: "act",
      });
      return {
        status: "ok",
        output: { previousMode, currentMode: "act" },
        durationMs: 0,
        metadata: { previousMode, currentMode: "act" },
      };
    },
  };
}
