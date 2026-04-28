import type { Tool } from "../Tool.js";

export function toolPermission(tool: Tool | null | undefined): Tool["permission"] | undefined {
  return tool?.permission;
}

export function isReadOnlyTool(toolName: string, tool: Tool | null | undefined): boolean {
  if (tool?.permission === "read" || tool?.permission === "meta") return true;
  return /^(FileRead|Grep|Glob|ArtifactRead|ArtifactList|TaskList|TaskGet|ExitPlanMode|AskUserQuestion)$/.test(
    toolName,
  );
}

export function toolNeedsConsent(toolName: string, tool: Tool | null | undefined): boolean {
  if (tool?.dangerous) return true;
  if (tool?.permission === "write" || tool?.permission === "execute" || tool?.permission === "net") {
    return true;
  }
  return /^(Bash|FileWrite|FileEdit|DocumentWrite|SpreadsheetWrite|FileDeliver|NotifyUser)$/.test(
    toolName,
  );
}
