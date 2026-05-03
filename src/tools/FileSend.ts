/**
 * FileSend — native tool for delivering workspace files to chat.
 *
 * Wraps file-send.sh as a first-class tool so models can call it
 * directly via tool_use without needing to know about Bash or scripts.
 *
 * Usage by model:
 *   FileSend({ path: "report.xlsx", channel: "General" })
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { execFile } from "child_process";
import { stat } from "fs/promises";
import path from "node:path";
import type { ChannelRef } from "../util/types.js";

export interface FileSendInput {
  path: string;
  channel?: string;
  caption?: string;
  mode?: "document" | "photo";
}

export interface FileSendOutput {
  id?: string;
  filename: string;
  marker?: string;
  channel?: ChannelRef;
  mode?: "document" | "photo";
}

export interface FileSendDeps {
  workspaceRoot: string;
  binDir: string;
  gatewayToken: string;
  botId: string;
  chatProxyUrl: string;
  getSourceChannel?: (ctx: ToolContext) => ChannelRef | null;
  sendFile?: (
    channel: ChannelRef,
    filePath: string,
    caption: string | undefined,
    mode: "document" | "photo",
  ) => Promise<void>;
}

function execScript(
  cmd: string,
  args: string[],
  env: Record<string, string>,
  timeoutMs: number,
): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve) => {
    execFile(cmd, args, {
      env: { ...process.env, ...env },
      timeout: timeoutMs,
    }, (err, stdout, stderr) => {
      resolve({
        stdout: stdout?.toString() || "",
        stderr: stderr?.toString() || "",
        code: err ? 1 : 0,
      });
    });
  });
}

export function makeFileSendTool(deps: FileSendDeps): Tool<FileSendInput, FileSendOutput> {
  return {
    name: "FileSend",
    description:
      "Send an existing workspace file to the user as a chat attachment. " +
      "Use this when the user asks you to send, deliver, attach, or share a file. " +
      "The file must exist in the workspace. Returns an attachment marker to include in your response.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description: "Workspace-relative path to the file (e.g. 'report.xlsx', 'docs/output.pdf')",
        },
        channel: {
          type: "string",
          description: "Channel name to send to (defaults to 'General')",
        },
        caption: {
          type: "string",
          description: "Optional caption for Telegram/Discord direct file delivery",
        },
        mode: {
          type: "string",
          enum: ["document", "photo"],
          description: "Delivery mode for Telegram/Discord. Defaults to document.",
        },
      },
      required: ["path"],
    },
    dangerous: false,
    permission: "net",

    validate(input) {
      if (!input?.path || typeof input.path !== "string") {
        return "`path` is required";
      }
      return null;
    },

    async execute(input, ctx): Promise<ToolResult<FileSendOutput>> {
      const start = Date.now();
      try {
        const resolved = path.resolve(deps.workspaceRoot, input.path);
        const rel = path.relative(deps.workspaceRoot, resolved);
        if (rel.startsWith("..") || path.isAbsolute(rel)) {
          return {
            status: "error",
            errorCode: "path_escape",
            errorMessage: "Path outside workspace",
            durationMs: Date.now() - start,
          };
        }

        try {
          const st = await stat(resolved);
          if (!st.isFile()) {
            return {
              status: "error",
              errorMessage: `Not a file: ${input.path}`,
              durationMs: Date.now() - start,
            };
          }
        } catch {
          return {
            status: "error",
            errorMessage: `File not found: ${input.path}`,
            durationMs: Date.now() - start,
          };
        }

        const sourceChannel = deps.getSourceChannel?.(ctx) ?? null;
        if (
          sourceChannel &&
          deps.sendFile &&
          (sourceChannel.type === "telegram" || sourceChannel.type === "discord")
        ) {
          const filename = path.basename(resolved);
          const mode = input.mode ?? "document";
          await deps.sendFile(sourceChannel, resolved, input.caption, mode);
          return {
            status: "ok",
            output: {
              filename,
              channel: sourceChannel,
              mode,
            },
            durationMs: Date.now() - start,
          };
        }

        const fileSendSh = path.join(deps.binDir, "file-send.sh");
        const channel = input.channel || "General";

        const { stdout, stderr, code } = await execScript(
          "sh",
          [fileSendSh, resolved, channel],
          {
            GATEWAY_TOKEN: deps.gatewayToken,
            BOT_ID: deps.botId,
            CHAT_PROXY_URL: deps.chatProxyUrl,
          },
          30000,
        );

        if (code !== 0) {
          return {
            status: "error",
            errorMessage: stderr || stdout || "file-send.sh failed",
            durationMs: Date.now() - start,
          };
        }

        // Parse attachment ID from output
        const idMatch = stdout.match(/"id":"([^"]+)"/);
        const markerMatch = stdout.match(/\[attachment:[^\]]+\]/);
        const filename = path.basename(resolved);

        if (!idMatch) {
          return {
            status: "error",
            errorMessage: `file-send.sh succeeded but no attachment ID in response: ${stdout.slice(0, 200)}`,
            durationMs: Date.now() - start,
          };
        }

        return {
          status: "ok",
          output: {
            id: idMatch[1]!,
            filename,
            marker: markerMatch?.[0] || `[attachment:${idMatch[1]}:${filename}]`,
          },
          durationMs: Date.now() - start,
        };
      } catch (error) {
        return {
          status: "error",
          errorMessage: error instanceof Error ? error.message : String(error),
          durationMs: Date.now() - start,
        };
      }
    },
  };
}
