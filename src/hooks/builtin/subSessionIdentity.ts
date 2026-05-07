/**
 * Built-in sub-session identity hook (port of
 * infra/docker/api-proxy/sub-session-identity.js).
 * Design reference: §6 invariant E (layered context).
 *
 * Injects an identity block at the head of the system prompt so the
 * LLM recognises its own sub-persona output (e.g. 변호두, Main Machine)
 * as its own instead of analysing it in the third person.
 */

import type { RegisteredHook } from "../types.js";

interface ParsedSessionKey {
  agent: string;
  kind: string | null;
  channelType: string | null;
  channelId: string | null;
}

function parseSessionKey(sessionKey: string): ParsedSessionKey | null {
  if (!sessionKey.startsWith("agent:")) return null;
  const parts = sessionKey.split(":");
  return {
    agent: parts[1] || "main",
    kind: parts[2] ?? null,
    channelType: parts[3] ?? null,
    channelId: parts.slice(4).join(":") || null,
  };
}

function buildIdentityHint(botId: string, sessionKey: string): string {
  const parsed = parseSessionKey(sessionKey);
  const personaLine = parsed
    ? `You are running as persona \`${parsed.agent}\`${parsed.kind ? ` in ${parsed.kind}` : ""}${parsed.channelType ? ` on channel \`${parsed.channelType}\`` : ""}.`
    : "You are running inside a Magi bot session.";

  return `<aef_session_identity priority="high">
Bot ID: \`${botId}\`
Session: \`${sessionKey || "(no session key)"}\`
${personaLine}

**Identity rules:**

1. Every assistant message in this conversation history — including
   messages attributed to sub-personas you operate (e.g. \`변호두\`,
   \`Main Machine\`, project-specific personas) — is YOUR OWN output.
   Do not analyze them as if they came from another bot or an external
   system.

2. When a user asks you about output that appeared in a different
   channel / session / persona under this same \`Bot ID\`, treat it as
   something YOU said. Own it in the first person. Do not slip into
   third-person diagnosis ("그 봇이 X했다", "that agent did Y").

3. If a past output of yours was wrong, acknowledge it as your own
   failure before proposing fixes. "내가 X를 놓쳤다" / "I missed X" —
   never "그 세션이 X를 놓쳤다" / "that session missed X".

4. When generating rules/prescriptions to prevent a recurrence, apply
   them to YOURSELF (update your own workspace files) rather than
   listing them as suggestions for "the other bot".
</aef_session_identity>`;
}

export const subSessionIdentityHook: RegisteredHook<"beforeLLMCall"> = {
  name: "builtin:sub-session-identity",
  point: "beforeLLMCall",
  priority: 10, // runs early so subsequent hooks see the augmented system
  blocking: true,
  timeoutMs: 100,
  handler: async ({ messages, tools, system, iteration }, ctx) => {
    // Only inject on the first iteration of the turn. Subsequent
    // iterations already carry the block in `system`.
    if (iteration > 0) return { action: "continue" };
    const hint = buildIdentityHint(ctx.botId, ctx.sessionKey);
    const nextSystem = system ? `${hint}\n\n${system}` : hint;
    return {
      action: "replace",
      value: { messages, tools, system: nextSystem, iteration },
    };
  },
};
