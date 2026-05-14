import { spawn } from "node:child_process";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";
import { withMagiBinPath } from "../util/shellPath.js";

export type KnowledgeWriteAction =
  | "add"
  | "update"
  | "delete"
  | "create-collection"
  | "delete-collection";

export type KnowledgeWriteScope = "personal" | "org";

export interface KnowledgeWriteInput {
  action: KnowledgeWriteAction;
  collection?: string;
  filename?: string;
  content?: string;
  scope?: KnowledgeWriteScope;
}

export interface KnowledgeWriteRunResult {
  exitCode: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
}

export type KnowledgeWriteRunner = (
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  extraEnv?: Record<string, string>,
) => Promise<KnowledgeWriteRunResult>;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["add", "update", "delete", "create-collection", "delete-collection"],
      description: "KB write operation.",
    },
    collection: {
      type: "string",
      description: "Collection name. Required for all actions except delete-collection (which uses 'collection' as the name to delete).",
    },
    filename: {
      type: "string",
      description: "Document filename. Required for add, update, delete.",
    },
    content: {
      type: "string",
      description: "Markdown content to write. Required for add and update.",
    },
    scope: {
      type: "string",
      enum: ["personal", "org"],
      description: "Where to write. 'personal' = this bot's private KB (default), 'org' = shared organization KB.",
    },
  },
  required: ["action"],
  additionalProperties: false,
} as const;

const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_OUTPUT_BYTES = 64 * 1024;

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

export function validateKnowledgeWriteInput(input: KnowledgeWriteInput): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return "`input` must be an object";
  }
  const validActions: KnowledgeWriteAction[] = [
    "add", "update", "delete", "create-collection", "delete-collection",
  ];
  if (!validActions.includes(input.action)) {
    return "`action` must be add, update, delete, create-collection, or delete-collection";
  }
  if (input.action === "create-collection" || input.action === "delete-collection") {
    if (!stringValue(input.collection)) {
      return "`collection` is required for create-collection and delete-collection";
    }
  }
  if (input.action === "add" || input.action === "update") {
    if (!stringValue(input.collection)) return "`collection` is required for add/update";
    if (!stringValue(input.filename)) return "`filename` is required for add/update";
    if (!stringValue(input.content)) return "`content` is required for add/update";
  }
  if (input.action === "delete") {
    if (!stringValue(input.collection)) return "`collection` is required for delete";
    if (!stringValue(input.filename)) return "`filename` is required for delete";
  }
  if (input.scope && !["personal", "org"].includes(input.scope)) {
    return "`scope` must be 'personal' or 'org'";
  }
  return null;
}

export function buildKnowledgeWriteArgs(input: KnowledgeWriteInput): string[] {
  switch (input.action) {
    case "create-collection":
      return ["--create-collection", stringValue(input.collection) ?? ""];
    case "delete-collection":
      return ["--delete-collection", stringValue(input.collection) ?? ""];
    case "add":
      return ["--add", stringValue(input.collection) ?? "", stringValue(input.filename) ?? "", stringValue(input.content) ?? ""];
    case "update":
      return ["--update", stringValue(input.collection) ?? "", stringValue(input.filename) ?? "", stringValue(input.content) ?? ""];
    case "delete":
      return ["--delete", stringValue(input.collection) ?? "", stringValue(input.filename) ?? ""];
  }
}

async function defaultRunner(
  args: string[],
  ctx: ToolContext,
  timeoutMs: number,
  extraEnv?: Record<string, string>,
): Promise<KnowledgeWriteRunResult> {
  const cwd = ctx.spawnWorkspace?.root ?? ctx.workspaceRoot;
  return new Promise<KnowledgeWriteRunResult>((resolve) => {
    const child = spawn("kb-write.sh", args, {
      cwd,
      env: {
        ...withMagiBinPath(process.env),
        PWD: cwd,
        BOT_ID: process.env.BOT_ID ?? ctx.botId,
        MAGI_WORKSPACE_ROOT: cwd,
        MAGI_BOT_ID: ctx.botId,
        ...extraEnv,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });

    const stdout = new Utf8StreamCapture(MAX_OUTPUT_BYTES);
    const stderr = new Utf8StreamCapture(MAX_OUTPUT_BYTES);

    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    }, timeoutMs);

    let settled = false;
    const abort = (): void => { child.kill("SIGTERM"); };
    ctx.abortSignal.addEventListener("abort", abort, { once: true });

    child.stdout.on("data", (chunk: Buffer) => stdout.write(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.write(chunk));
    child.on("close", (exitCode, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ctx.abortSignal.removeEventListener("abort", abort);
      resolve({ exitCode, signal, stdout: stdout.end(), stderr: stderr.end() });
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ctx.abortSignal.removeEventListener("abort", abort);
      resolve({
        exitCode: null,
        signal: null,
        stdout: stdout.end(),
        stderr: error instanceof Error ? error.message : String(error),
      });
    });
  });
}

export function makeKnowledgeWriteTool(opts: {
  name?: "knowledge-write" | "KnowledgeWrite";
  runner?: KnowledgeWriteRunner;
} = {}): Tool<KnowledgeWriteInput, string> {
  const runner = opts.runner ??
    ((args: string[], ctx: ToolContext, timeoutMs: number, extraEnv?: Record<string, string>) =>
      defaultRunner(args, ctx, timeoutMs, extraEnv));
  return {
    name: opts.name ?? "knowledge-write",
    description:
      "Write to the user's Knowledge Base — add/update/delete documents and manage collections. Use scope='org' to write to the shared organization KB, or 'personal' (default) for this bot's private KB.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    dangerous: false,
    tags: ["knowledge", "kb", "write"],
    validate(input) {
      return validateKnowledgeWriteInput(input as KnowledgeWriteInput);
    },
    async execute(input: KnowledgeWriteInput, ctx: ToolContext): Promise<ToolResult<string>> {
      const start = Date.now();
      const validation = validateKnowledgeWriteInput(input);
      if (validation) {
        return {
          status: "error",
          errorCode: "invalid_input",
          errorMessage: validation,
          durationMs: Date.now() - start,
        };
      }
      const args = buildKnowledgeWriteArgs(input);
      const scopeEnv = input.scope ? { KB_SCOPE: input.scope } : undefined;
      const result = await runner(args, ctx, DEFAULT_TIMEOUT_MS, scopeEnv);
      const output = result.stdout.trim();
      const error = result.stderr.trim();
      return {
        status: result.exitCode === 0 ? "ok" : "error",
        output: result.exitCode === 0 ? output : undefined,
        errorCode: result.exitCode === 0
          ? undefined
          : result.exitCode === null
            ? "spawn_error"
            : `exit_${result.exitCode}`,
        errorMessage: result.exitCode === 0
          ? undefined
          : error || output || `kb-write.sh exited ${result.exitCode}`,
        durationMs: Date.now() - start,
        metadata: {
          args: args.slice(0, 3),
          scope: input.scope ?? "personal",
          signal: result.signal,
        },
      };
    },
  };
}
