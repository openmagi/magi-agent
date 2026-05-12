/**
 * builtin:shadow-checkpoint — afterToolUse hook that creates shadow
 * git checkpoints after workspace-mutating tool calls.
 *
 * Non-blocking (blocking: false, priority: 90) — never affects the
 * turn. Gated by MAGI_CHECKPOINT=1 env var.
 */

import type { RegisteredHook } from "../types.js";
import { ShadowGit } from "../../checkpoint/ShadowGit.js";
import type { CheckpointMeta } from "../../checkpoint/ShadowGit.js";
import {
  shouldPruneInline,
  pruneCheckpoints,
  DEFAULT_PRUNE_POLICY,
} from "../../checkpoint/ShadowGitPruning.js";

export interface ShadowCheckpointOptions {
  workspaceRoot: string;
  enabled?: boolean;
}

const ALWAYS_CHECKPOINT_TOOLS = new Set([
  "FileWrite",
  "FileEdit",
  "DocumentWrite",
  "SpreadsheetWrite",
  "CommitCheckpoint",
]);

const CONDITIONAL_TOOLS = new Set(["Bash", "SafeCommand"]);

const SPAWN_TOOLS = new Set(["SpawnAgent"]);

const SKIP_TOOLS = new Set([
  "FileRead",
  "Glob",
  "Grep",
  "Browser",
  "WebSearch",
  "KnowledgeSearch",
  "ArtifactRead",
  "ArtifactList",
  "Clock",
  "Calculation",
  "CodeDiagnostics",
  "CodeSymbolSearch",
  "AskUserQuestion",
]);

function extractFilesFromToolInput(
  toolName: string,
  input: unknown,
): string[] {
  if (!input || typeof input !== "object") return [];
  const inp = input as Record<string, unknown>;
  if (typeof inp["path"] === "string") return [inp["path"]];
  if (typeof inp["file_path"] === "string") return [inp["file_path"]];
  if (typeof inp["filePath"] === "string") return [inp["filePath"]];
  return [];
}

function shouldCheckpoint(
  toolName: string,
): "always" | "conditional" | "spawn" | "skip" {
  if (ALWAYS_CHECKPOINT_TOOLS.has(toolName)) return "always";
  if (CONDITIONAL_TOOLS.has(toolName)) return "conditional";
  if (SPAWN_TOOLS.has(toolName)) return "spawn";
  return "skip";
}

const PRUNE_INTERVAL = 50;

export function makeShadowCheckpointHook(
  opts: ShadowCheckpointOptions,
): RegisteredHook<"afterToolUse"> {
  const enabled =
    opts.enabled ??
    (process.env["MAGI_CHECKPOINT"] === "1");

  const pruneEnabled = process.env["MAGI_CHECKPOINT_PRUNE"] === "1";

  const maxSizeBytes = process.env["MAGI_CHECKPOINT_MAX_SIZE"]
    ? parseInt(process.env["MAGI_CHECKPOINT_MAX_SIZE"], 10)
    : DEFAULT_PRUNE_POLICY.maxSizeBytes;

  const shadowGit = new ShadowGit({
    workspaceRoot: opts.workspaceRoot,
  });

  let checkpointCounter = 0;

  return {
    name: "builtin:shadow-checkpoint",
    point: "afterToolUse",
    priority: 90,
    blocking: false,
    timeoutMs: 5_000,
    handler: async (args, ctx) => {
      if (!enabled) return;

      const action = shouldCheckpoint(args.toolName);
      if (action === "skip") return;

      // Skip on tool error
      if (args.result.status === "error") return;

      // Conditional tools: check workspace dirty state
      if (action === "conditional") {
        const { runShadowGit } = await import("../../checkpoint/ShadowGit.js");
        const status = await runShadowGit(
          opts.workspaceRoot,
          ["status", "--porcelain"],
          3_000,
        );
        const dirty = status.stdout
          .split("\n")
          .some((l) => l.trim().length > 0);
        if (!dirty) return;
      }

      const meta: CheckpointMeta = {
        toolName: args.toolName,
        turnId: ctx.turnId,
        sessionKey: ctx.sessionKey,
        timestamp: Date.now(),
        filesHint: extractFilesFromToolInput(args.toolName, args.input),
      };

      try {
        const sha = await shadowGit.createCheckpoint(meta);
        if (sha) {
          checkpointCounter++;
          ctx.log("info", "shadow checkpoint created", {
            sha: sha.slice(0, 8),
            tool: args.toolName,
            count: checkpointCounter,
          });

          // Inline pruning (async, non-blocking)
          if (pruneEnabled && shouldPruneInline(checkpointCounter, PRUNE_INTERVAL)) {
            pruneCheckpoints(opts.workspaceRoot, {
              ...DEFAULT_PRUNE_POLICY,
              maxSizeBytes,
            }).then((result) => {
              if (result.pruned > 0) {
                ctx.log("info", "shadow checkpoint pruned", {
                  pruned: result.pruned,
                  emergency: result.emergency,
                  after: result.afterCount,
                });
              }
            }).catch((err) => {
              ctx.log("warn", "shadow checkpoint prune failed", {
                error: String(err),
              });
            });
          }
        }
      } catch (err) {
        ctx.log("warn", "shadow checkpoint failed", {
          error: String(err),
          tool: args.toolName,
        });
      }
    },
  };
}
