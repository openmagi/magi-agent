import type { HookContext, RegisteredHook } from "../types.js";

function commandFromInput(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const command = (input as { command?: unknown }).command;
  return typeof command === "string" ? command : "";
}

function deniedGitPattern(command: string): string | null {
  const normalized = command.replace(/\s+/g, " ").trim();
  if (/\bgit\s+reset\s+--hard(?:\s|$)/i.test(normalized)) {
    return "git reset --hard";
  }
  if (/\bgit\s+checkout\s+--\s+/i.test(normalized)) {
    return "git checkout --";
  }
  if (/\bgit\s+clean\s+-[^\n]*[fd][^\n]*[fd]/i.test(normalized)) {
    return "git clean -fd";
  }
  if (/\bgit\s+push\b[^\n]*(?:--force|-f)(?:\s|$)/i.test(normalized)) {
    return "git push --force";
  }
  if (/\brm\s+-[^\n]*r[^\n]*f[^\n]*\.git\b/i.test(normalized)) {
    return "rm -rf .git";
  }
  return null;
}

export function makeGitSafetyGateHook(): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:git-safety-gate",
    point: "beforeToolUse",
    priority: 43,
    blocking: true,
    timeoutMs: 1_000,
    handler: async ({ toolName, input }, _ctx: HookContext) => {
      if (toolName !== "Bash") return { action: "continue" };
      const command = commandFromInput(input);
      const denied = deniedGitPattern(command);
      if (!denied) return { action: "continue" };
      return {
        action: "block",
        reason: [
          `[RETRY:GIT_SAFETY] Blocked destructive git command: ${denied}.`,
          "Use GitDiff/Git status inspection first, then ask the user explicitly before destructive git cleanup or history rewriting.",
        ].join("\n"),
      };
    },
  };
}
