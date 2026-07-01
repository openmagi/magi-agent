// Agent MODE (posture) — client-side shared types.
//
// A *mode* is an explicit, user-selected, session-sticky posture, mirroring the
// runtime payload shape (`magi_agent/customize/modes.py` `AgentMode.to_payload`).
// It carries a soft system prompt, a tool allow/deny DELTA from the bot default,
// and the ids of scoped policies active in this mode.
//
// The composer sends the active mode id as the per-turn `agentMode` request
// field; the runtime resolves it into the assembled system prompt
// (`runtime.message_builder._agent_mode_block`) and narrows the exposed toolset
// via `tool_delta.exclude` (`cli.wiring._agent_mode_excluded_tool_names`).

export interface AgentModeToolDelta {
  exclude: string[];
  include: string[];
}

/** Full mode record as returned by `GET/PUT /v1/app/modes`. */
export interface AgentMode {
  id: string;
  displayName: string;
  systemPrompt: string;
  toolDelta: AgentModeToolDelta;
  scopedPolicyIds: string[];
}

/**
 * Minimal shape the composer selector needs — id + display name only. The
 * composer never renders the system prompt or tool delta; it just picks which
 * mode id to send. Keeping this narrow means the selector re-renders cheaply
 * and the send path never depends on the heavy fields.
 */
export interface AgentModeSummary {
  id: string;
  displayName: string;
}

export function toAgentModeSummary(mode: AgentMode): AgentModeSummary {
  return { id: mode.id, displayName: mode.displayName };
}
