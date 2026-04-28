import type { Tool, ToolRegistry } from "../Tool.js";

export interface ToolAccessAllowed {
  allowed: true;
  tool: Tool;
}

export interface ToolAccessDenied {
  allowed: false;
  reason: "unknown_tool" | "not_exposed";
  availableNames: string[];
  message: string;
}

export type ToolAccessDecision = ToolAccessAllowed | ToolAccessDenied;

export interface ToolArbiterInput {
  registry: ToolRegistry;
  toolName: string;
  exposedToolNames?: readonly string[];
}

export function decideToolAccess(input: ToolArbiterInput): ToolAccessDecision {
  const registryTool = input.registry.resolve(input.toolName);
  const availableNames = availableToolNames(input.registry, input.exposedToolNames);

  if (!registryTool) {
    return {
      allowed: false,
      reason: "unknown_tool",
      availableNames,
      message: buildUnknownToolMessage(input.toolName, availableNames),
    };
  }

  if (
    input.exposedToolNames !== undefined &&
    !input.exposedToolNames.includes(input.toolName)
  ) {
    return {
      allowed: false,
      reason: "not_exposed",
      availableNames,
      message: buildUnknownToolMessage(input.toolName, availableNames),
    };
  }

  return { allowed: true, tool: registryTool };
}

export function availableToolNames(
  registry: ToolRegistry,
  exposedToolNames?: readonly string[],
): string[] {
  const sourceNames =
    exposedToolNames !== undefined
      ? exposedToolNames
      : registry
          .list()
          .map((tool) => tool.name)
          .filter((name): name is string => typeof name === "string" && name.length > 0);
  return Array.from(new Set(sourceNames)).sort();
}

export function buildUnknownToolMessage(
  toolName: string,
  availableNames: readonly string[],
): string {
  const preview = availableNames.slice(0, 20).join(", ");
  const suffix =
    availableNames.length > 20
      ? `, ... (+${availableNames.length - 20} more)`
      : "";
  const listText = availableNames.length > 0 ? `${preview}${suffix}` : "(none)";
  return `Unknown tool: ${toolName}. Available tools: ${listText}.`;
}
