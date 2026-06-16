import type { ChannelState, SubagentActivity } from "./types";

function isActiveSubagent(status: SubagentActivity["status"]): boolean {
  return status === "running" || status === "waiting";
}

function normalizedSubagentRole(role: string): string {
  return role.trim().toLowerCase();
}

export function isForegroundSubagent(subagent: SubagentActivity): boolean {
  const role = normalizedSubagentRole(subagent.role);
  return role !== "bash" && role !== "background";
}

export function activeForegroundSubagentCount(
  state: Partial<ChannelState> | null | undefined,
): number {
  return (state?.subagents ?? []).filter(
    (subagent) => isActiveSubagent(subagent.status) && isForegroundSubagent(subagent),
  ).length;
}

export function hasActiveForegroundSubagents(
  state: Partial<ChannelState> | null | undefined,
): boolean {
  return activeForegroundSubagentCount(state) > 0;
}
