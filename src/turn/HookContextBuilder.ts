/**
 * HookContextBuilder — build the read-only HookContext passed to
 * every lifecycle handler. Extracted from Turn (R3 refactor).
 */

import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { HookContext, HookPoint } from "../hooks/types.js";

export function buildHookContext(
  session: Session,
  sse: SseWriter,
  turnId: string,
  point: HookPoint,
  agentModel = session.agent.config.model,
  abortSignal?: AbortSignal,
): HookContext {
  const agent = session.agent;
  return {
    botId: agent.config.botId,
    userId: agent.config.userId,
    sessionKey: session.meta.sessionKey,
    turnId,
    llm: agent.llm,
    agentModel,
    providerHealth: typeof agent.llm.getLastProviderHealth === "function"
      ? agent.llm.getLastProviderHealth()
      : null,
    transcript: [],
    emit: (event) => sse.agent(event),
    log: (level, msg, data) => {
      const prefix = `[hook:${point}]`;
      if (level === "error") console.error(prefix, msg, data ?? {});
      else if (level === "warn") console.warn(prefix, msg, data ?? {});
      else console.log(prefix, msg, data ?? {});
    },
    abortSignal: abortSignal ?? new AbortController().signal,
    deadlineMs: 5_000,
  };
}
