import type { Tool } from "../Tool.js";
import { classifyPathSafety } from "./PathSafetyPolicy.js";
import {
  classifyShellSafety,
  classifyWorkspaceShellBoundary,
} from "./ShellSafetyPolicy.js";
import { isReadOnlyTool, toolNeedsConsent } from "./ToolPermissionAdapters.js";

export type PermissionMode = "default" | "plan" | "auto" | "bypass" | "workspace-bypass";
export type PermissionSource = "turn" | "mcp" | "child-agent";

export type PermissionDecision =
  | {
      decision: "allow";
      reason: string;
      updatedInput?: unknown;
    }
  | {
      decision: "deny";
      reason: string;
      securityCritical: boolean;
    }
  | {
      decision: "ask";
      reason: string;
      proposedInput?: unknown;
    };

export interface PermissionArbiterInput {
  mode: PermissionMode;
  source: PermissionSource;
  toolName: string;
  input: unknown;
  tool?: Tool | null;
  workspaceRoot: string;
}

export interface PermissionArbiterStatus {
  enabled: true;
  bypassDeniedCount: number;
  lastDeniedReasons: string[];
}

let bypassDeniedCount = 0;
const lastDeniedReasons: string[] = [];

export async function decideRuntimePermission(
  input: PermissionArbiterInput,
): Promise<PermissionDecision> {
  const security = securityDecision(input);
  if (security) return security;

  if (input.mode === "plan" && input.toolName === "PatchApply") {
    if (isPatchApplyDryRun(input.input)) {
      return { decision: "allow", reason: "PatchApply dry-run is allowed in plan mode" };
    }
    return {
      decision: "ask",
      reason: "Review PatchApply changes before applying.",
      proposedInput: input.input,
    };
  }

  if (input.mode === "plan" && !isReadOnlyTool(input.toolName, input.tool)) {
    return {
      decision: "deny",
      reason: `tool ${input.toolName} is not available in plan mode`,
      securityCritical: false,
    };
  }

  if (isBypassLikeMode(input.mode)) {
    return {
      decision: "allow",
      reason:
        input.mode === "workspace-bypass"
          ? "workspace-bypass mode after workspace boundary policy"
          : "bypass mode after security policy",
    };
  }

  if (input.source === "child-agent" && input.tool?.dangerous !== true) {
    return { decision: "allow", reason: "child-agent spawn workspace after security policy" };
  }

  if (input.mode === "auto" && !toolNeedsConsent(input.toolName, input.tool)) {
    return { decision: "allow", reason: "auto mode safe tool" };
  }

  if (toolNeedsConsent(input.toolName, input.tool)) {
    return {
      decision: "ask",
      reason: `permission required for ${input.toolName}`,
      proposedInput: input.input,
    };
  }

  return { decision: "allow", reason: "tool is read-only or metadata-only" };
}

export function permissionArbiterStatus(): PermissionArbiterStatus {
  return {
    enabled: true,
    bypassDeniedCount,
    lastDeniedReasons: [...lastDeniedReasons],
  };
}

export function resetPermissionArbiterStatusForTests(): void {
  bypassDeniedCount = 0;
  lastDeniedReasons.splice(0);
}

function securityDecision(input: PermissionArbiterInput): PermissionDecision | null {
  if (input.toolName === "Bash") {
    const command = commandOf(input.input);
    const shell =
      input.mode === "workspace-bypass"
        ? classifyWorkspaceShellBoundary(command)
        : classifyShellSafety(command);
    if (!shell.safe) {
      if (isBypassLikeMode(input.mode) || isSecurityCriticalShellReason(shell.reason)) {
        recordDeny(input, shell.reason ?? "unsafe shell command");
        return {
          decision: "deny",
          reason: shell.reason ?? "unsafe shell command",
          securityCritical: true,
        };
      }
      return {
        decision: "ask",
        reason: shell.reason ?? "complex shell requires explicit approval",
        proposedInput: input.input,
      };
    }
  }

  if (input.toolName === "FileRead" || input.toolName === "FileWrite" || input.toolName === "FileEdit") {
    const filePath = pathOf(input.input);
    const pathSafety = classifyPathSafety({
      workspaceRoot: input.workspaceRoot,
      filePath,
      operation: input.toolName === "FileRead" ? "read" : "write",
      allowWorkspaceSecretPaths: input.mode === "workspace-bypass",
    });
    if (pathSafety.classification !== "workspace_safe") {
      recordDeny(input, pathSafety.reason ?? pathSafety.classification);
      return {
        decision: "deny",
        reason: pathSafety.reason ?? pathSafety.classification,
        securityCritical: true,
      };
    }
  }

  return null;
}

function commandOf(input: unknown): string {
  if (input && typeof input === "object" && "command" in input) {
    const command = (input as { command?: unknown }).command;
    return typeof command === "string" ? command : "";
  }
  return "";
}

function pathOf(input: unknown): string {
  if (input && typeof input === "object" && "path" in input) {
    const p = (input as { path?: unknown }).path;
    return typeof p === "string" ? p : "";
  }
  return "";
}

function isPatchApplyDryRun(input: unknown): boolean {
  return !!input && typeof input === "object" && (input as { dry_run?: unknown }).dry_run === true;
}

function isSecurityCriticalShellReason(reason: string | undefined): boolean {
  if (!reason) return false;
  return !/complex shell/.test(reason);
}

function isBypassLikeMode(mode: PermissionMode): boolean {
  return mode === "bypass" || mode === "workspace-bypass";
}

function recordDeny(input: PermissionArbiterInput, reason: string): void {
  if (isBypassLikeMode(input.mode)) bypassDeniedCount += 1;
  lastDeniedReasons.unshift(`${input.toolName}: ${reason}`);
  lastDeniedReasons.splice(10);
}
