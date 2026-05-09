/**
 * fileDeliveryInterceptor — deterministic file delivery hook.
 *
 * Uses the shared request meta classifier to detect "send/deliver this
 * file" intent. When detected and the file exists in workspace, executes
 * file-send.sh or Telegram sendDocument directly — bypassing the main
 * model entirely.
 *
 * This is the harness-level solution: models (especially non-Claude ones
 * like Kimi K2.6) unreliably distinguish "send the file" from "read and
 * summarize." The hook makes file delivery deterministic.
 */

import type { RegisteredHook, HookContext, HookArgs, HookResult } from "../types.js";
import { execFile } from "child_process";
import { stat } from "fs/promises";
import { join, resolve, basename } from "path";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";
import type { ChannelRef } from "../../util/types.js";
import type { ChannelDeliveryReceipt } from "../../channels/ChannelAdapter.js";

// ── File delivery execution ──

function execScript(
  cmd: string,
  args: string[],
  env: Record<string, string>,
  timeoutMs: number,
): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((res) => {
    execFile(cmd, args, { env: { ...process.env, ...env }, timeout: timeoutMs }, (err, stdout, stderr) => {
      res({
        stdout: stdout?.toString() || "",
        stderr: stderr?.toString() || "",
        code: err ? 1 : 0,
      });
    });
  });
}

// ── Hook ──

export interface FileDeliveryInterceptorOptions {
  workspaceRoot: string;
  channel?: string | null;
  gatewayToken?: string;
  botId?: string;
  chatProxyUrl?: string;
  telegramBotToken?: string;
  telegramChatId?: string;
  getSourceChannel?: (ctx: HookContext) => ChannelRef | null;
  sendFile?: (
    channel: ChannelRef,
    filePath: string,
    caption: string | undefined,
    mode: "document" | "photo",
  ) => Promise<ChannelDeliveryReceipt>;
}

function formatNativeDeliveryResult(fileName: string, channel: ChannelRef, receipt: ChannelDeliveryReceipt): string {
  const channelLabel = channel.type === "telegram" ? "Telegram" : "Discord";
  if (receipt.messageId) {
    return `File "${fileName}" sent to ${channelLabel} chat with provider message id ${receipt.messageId}.`;
  }
  return `File "${fileName}" send was accepted by ${channelLabel}, but no provider message id was returned.`;
}

export function fileDeliveryInterceptor(
  opts: FileDeliveryInterceptorOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:file-delivery-interceptor",
    point: "beforeLLMCall",
    priority: 1,
    blocking: true,
    handler: async (
      args: HookArgs["beforeLLMCall"],
      ctx: HookContext,
    ): Promise<HookResult<HookArgs["beforeLLMCall"]> | void> => {
      // Only intercept on first iteration
      if (args.iteration !== 0) return;

      // Extract latest user message
      const lastUserMsg = [...args.messages]
        .reverse()
        .find((m) => m.role === "user");
      if (!lastUserMsg) return;

      const userText =
        typeof lastUserMsg.content === "string"
          ? lastUserMsg.content
          : Array.isArray(lastUserMsg.content)
            ? (lastUserMsg.content as Array<{ type: string; text?: string }>)
                .filter((b) => b.type === "text")
                .map((b) => b.text || "")
                .join(" ")
            : "";

      if (!userText || userText.length > 500) return; // Skip long messages

      // Quick pre-filter: must mention a file extension somewhere
      if (!/\.(?:md|pdf|xlsx?|docx?|csv|txt|json|pptx?|hwpx?|html|png|jpg|jpeg)/i.test(userText)) {
        return;
      }

      const result = await getOrClassifyRequestMeta(ctx, { userMessage: userText });
      if (result.fileDelivery.intent !== "deliver_existing" || !result.fileDelivery.path) {
        return;
      }

      // Resolve file path safely
      const resolved = resolve(opts.workspaceRoot, result.fileDelivery.path);
      if (!resolved.startsWith(opts.workspaceRoot)) {
        ctx.log(
          "warn",
          `[file-delivery-interceptor] Path traversal rejected: ${result.fileDelivery.path}`,
        );
        return;
      }

      // Verify file exists
      try {
        const st = await stat(resolved);
        if (!st.isFile()) return;
      } catch {
        return; // File not found — let model handle it
      }

      const fileName = basename(resolved);
      ctx.log("info", `[file-delivery-interceptor] Delivering ${fileName} (classified by Haiku)`);

      // Execute delivery
      let deliveryResult: string;
      const sourceChannel = opts.getSourceChannel?.(ctx) ?? null;
      if (
        sourceChannel &&
        opts.sendFile &&
        (sourceChannel.type === "telegram" || sourceChannel.type === "discord")
      ) {
        try {
          const receipt = await opts.sendFile(sourceChannel, resolved, fileName, "document");
          deliveryResult = formatNativeDeliveryResult(fileName, sourceChannel, receipt);
        } catch (err) {
          deliveryResult = `${sourceChannel.type === "telegram" ? "Telegram" : "Discord"} delivery error: ${(err as Error).message}`;
        }
      } else {
        const channelName = sourceChannel?.type === "app"
          ? sourceChannel.channelId
          : opts.channel;
        const isWebChannel = !!channelName;

        if (isWebChannel) {
          const binDir = join(opts.workspaceRoot, "..", "bin");
          const fileSendSh = join(binDir, "file-send.sh");

          try {
            const { stdout, stderr, code } = await execScript(
              "sh",
              [fileSendSh, resolved, channelName || "General"],
              {
                GATEWAY_TOKEN: opts.gatewayToken || "",
                BOT_ID: opts.botId || "",
                CHAT_PROXY_URL: opts.chatProxyUrl || "http://chat-proxy.magi-system.svc.cluster.local:3002",
              },
              30000,
            );
            const marker = stdout.match(/\[attachment:[^\]\r\n]+\]/)?.[0];
            deliveryResult = code === 0
              ? marker
                ? `File "${fileName}" uploaded as chat attachment marker ${marker}.`
                : `File "${fileName}" upload was accepted by chat attachment service, but no attachment marker was returned.`
              : `File delivery failed: ${stderr || stdout}`;
          } catch (err) {
            deliveryResult = `File delivery error: ${(err as Error).message}`;
          }
        } else if (opts.telegramBotToken && opts.telegramChatId) {
          try {
            const { stdout } = await execScript(
              "curl",
              [
                "-sf",
                "-X", "POST",
                `https://api.telegram.org/bot${opts.telegramBotToken}/sendDocument`,
                "-F", `chat_id=${opts.telegramChatId}`,
                "-F", `document=@${resolved}`,
                "-F", `caption=${fileName}`,
              ],
              {},
              30000,
            );
            const parsed = JSON.parse(stdout || "{}");
            deliveryResult = parsed.ok
              ? `File "${fileName}" sent to Telegram chat.`
              : `Telegram delivery failed: ${parsed.description || "unknown error"}`;
          } catch (err) {
            deliveryResult = `Telegram delivery error: ${(err as Error).message}`;
          }
        } else {
          return; // No delivery channel — let model handle
        }
      }

      ctx.emit({ type: "text_delta", delta: `📎 ${deliveryResult}\n` });

      // Inject result into system prompt so LLM just confirms the evidence.
      return {
        action: "replace",
        value: {
          ...args,
          system: `${args.system}\n\n[SYSTEM: Runtime attempted file delivery before the model response. Result: ${deliveryResult}. Briefly report this exact result to the user. If the result includes a provider message id or attachment marker, mention it. Do NOT ask the user to confirm receipt as a completion condition. Do NOT re-read or summarize the file.]`,
        },
      };
    },
  };
}
