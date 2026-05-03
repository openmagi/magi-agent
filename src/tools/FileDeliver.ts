import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type { OutputArtifactRegistry } from "../output/OutputArtifactRegistry.js";
import type { ChannelRef } from "../util/types.js";
import type {
  DeliveryStatus,
  DeliveryTarget,
  OutputArtifactRecord,
} from "../output/outputTypes.js";
import { errorResult } from "../util/toolResult.js";

export interface FileDeliverInput {
  artifactId?: string;
  path?: string;
  target: "chat" | "kb" | "both";
  chat?: {
    channel?: string;
    caption?: string;
  };
  kb?: {
    collection?: string;
  };
}

export interface FileDeliverOutput {
  deliveries: Array<{
    target: DeliveryTarget;
    status: DeliveryStatus;
    externalId?: string;
    marker?: string;
    attemptCount: number;
  }>;
}

export interface FileDeliverDeps {
  workspaceRoot: string;
  outputRegistry: OutputArtifactRegistry;
  chatProxyUrl: string;
  gatewayToken: string;
  fetchImpl?: typeof fetch;
  sleepImpl?: (ms: number) => Promise<void>;
  getSourceChannel?: (ctx: ToolContext) => ChannelRef | null;
  sendFile?: (
    channel: ChannelRef,
    filePath: string,
    caption: string | undefined,
    mode: "document" | "photo",
  ) => Promise<void>;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    artifactId: { type: "string", description: "ID of a registered output artifact" },
    path: { type: "string", description: "Workspace-relative path to an existing file (alternative to artifactId)" },
    target: { type: "string", enum: ["chat", "kb", "both"] },
    chat: {
      type: "object",
      properties: {
        channel: { type: "string" },
        caption: { type: "string" },
      },
    },
    kb: {
      type: "object",
      properties: {
        collection: { type: "string" },
      },
    },
  },
  required: ["target"],
} as const;

const RETRY_DELAYS_MS = [0, 10_000, 30_000] as const;

function isTransientHttpStatus(status: number): boolean {
  return status === 408 || status === 409 || status === 425 || status === 429 || status >= 500;
}

function isTransientError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  if (error.name === "AbortError") {
    return false;
  }
  const text = `${error.name} ${error.message}`.toLowerCase();
  return (
    text.includes("timeout") ||
    text.includes("temporar") ||
    text.includes("unavailable") ||
    text.includes("network") ||
    text.includes("fetch failed") ||
    text.includes("connection reset")
  );
}

function toTargets(target: FileDeliverInput["target"]): DeliveryTarget[] {
  return target === "both" ? ["chat", "kb"] : [target];
}

async function readArtifactBytes(
  workspaceRoot: string,
  artifact: OutputArtifactRecord,
): Promise<Uint8Array> {
  return fs.readFile(path.join(workspaceRoot, artifact.workspacePath));
}

async function deliverToChat(
  deps: FileDeliverDeps,
  artifact: OutputArtifactRecord,
  bytes: Uint8Array,
  input: FileDeliverInput,
  ctx: ToolContext,
  filePath: string,
): Promise<{ externalId: string; marker?: string }> {
  const sourceChannel = deps.getSourceChannel?.(ctx) ?? null;
  if (
    sourceChannel &&
    deps.sendFile &&
    (sourceChannel.type === "telegram" || sourceChannel.type === "discord")
  ) {
    await deps.sendFile(
      sourceChannel,
      filePath,
      input.chat?.caption,
      "document",
    );
    return {
      externalId: `${sourceChannel.type}:${sourceChannel.channelId}`,
    };
  }

  const form = new FormData();
  form.append(
    "file",
    new Blob([bytes], { type: artifact.mimeType || "application/octet-stream" }),
    artifact.filename,
  );
  form.append("channel_name", input.chat?.channel || "general");
  if (input.chat?.caption) {
    form.append("caption", input.chat.caption);
  }

  const response = await (deps.fetchImpl ?? fetch)(
    `${deps.chatProxyUrl.replace(/\/$/, "")}/v1/bot-channels/attachment`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${deps.gatewayToken}`,
      },
      body: form,
      signal: ctx.abortSignal,
    },
  );

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw Object.assign(
      new Error(text || `chat delivery failed: HTTP ${response.status}`),
      { status: response.status },
    );
  }

  const payload = (await response.json()) as { id?: string };
  if (!payload.id) {
    throw new Error("chat delivery response missing attachment id");
  }
  return {
    externalId: payload.id,
    marker: `[attachment:${payload.id}:${artifact.filename}]`,
  };
}

async function deliverToKb(
  deps: FileDeliverDeps,
  artifact: OutputArtifactRecord,
  bytes: Uint8Array,
  input: FileDeliverInput,
  ctx: ToolContext,
): Promise<{ externalId: string; marker?: string }> {
  const collection = input.kb?.collection || "artifacts";
  const response = await (deps.fetchImpl ?? fetch)(
    `${deps.chatProxyUrl.replace(/\/$/, "")}/v1/integrations/knowledge-write/upload-file`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${deps.gatewayToken}`,
      },
      body: JSON.stringify({
        collection,
        filename: artifact.filename,
        mime_type: artifact.mimeType,
        content_base64: Buffer.from(bytes).toString("base64"),
      }),
      signal: ctx.abortSignal,
    },
  );

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw Object.assign(
      new Error(text || `kb delivery failed: HTTP ${response.status}`),
      { status: response.status },
    );
  }

  const payload = (await response.json()) as { collection?: string; filename?: string };
  return {
    externalId: `${payload.collection || collection}/${payload.filename || artifact.filename}`,
  };
}

async function deliverWithRetry(
  target: DeliveryTarget,
  deps: FileDeliverDeps,
  artifact: OutputArtifactRecord,
  bytes: Uint8Array,
  input: FileDeliverInput,
  ctx: ToolContext,
  trackRegistry: boolean,
  filePath: string,
): Promise<{ target: DeliveryTarget; status: DeliveryStatus; externalId?: string; marker?: string; attemptCount: number }> {
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
    if (attempt > 1) {
      const waitMs = RETRY_DELAYS_MS[attempt - 1] ?? 0;
      ctx.emitProgress({
        label: `${target} delivery retry ${attempt - 1}/${RETRY_DELAYS_MS.length - 1}`,
      });
      await (deps.sleepImpl ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms))))(
        waitMs,
      );
    }

    if (attempt === 1 && trackRegistry) {
      await deps.outputRegistry.markDeliveryPending(artifact.artifactId, {
        target,
        attemptCount: 1,
      });
    }

    try {
      const delivered =
        target === "chat"
          ? await deliverToChat(deps, artifact, bytes, input, ctx, filePath)
          : await deliverToKb(deps, artifact, bytes, input, ctx);

      if (trackRegistry) {
        await deps.outputRegistry.markDeliveryResult(artifact.artifactId, {
          target,
          attemptCount: attempt,
          status: "sent",
          externalId: delivered.externalId,
          marker: delivered.marker,
        });
      }

      return {
        target,
        status: "sent",
        externalId: delivered.externalId,
        marker: delivered.marker,
        attemptCount: attempt,
      };
    } catch (error) {
      lastError = error;
      const status = typeof error === "object" && error && "status" in error
        ? Number((error as { status?: number }).status)
        : undefined;
      const transient = (status !== undefined && isTransientHttpStatus(status)) || isTransientError(error);

      if (transient && attempt < RETRY_DELAYS_MS.length) {
        if (trackRegistry) {
          await deps.outputRegistry.markDeliveryResult(artifact.artifactId, {
            target,
            attemptCount: attempt,
            status: "retrying",
            errorMessage: error instanceof Error ? error.message : String(error),
          });
        }
        continue;
      }

      if (trackRegistry) {
        await deps.outputRegistry.markDeliveryResult(artifact.artifactId, {
          target,
          attemptCount: attempt,
          status: "failed",
          errorMessage: error instanceof Error ? error.message : String(error),
        });
      }

      throw error;
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

export function makeFileDeliverTool(deps: FileDeliverDeps): Tool<FileDeliverInput, FileDeliverOutput> {
  return {
    name: "FileDeliver",
    description:
      "Deliver a file to chat attachments, KB storage, or both. Use `artifactId` for registered output artifacts, or `path` for any existing workspace file (e.g. reports, documents, spreadsheets).",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    validate(input) {
      if ((!input?.artifactId || typeof input.artifactId !== "string") &&
          (!input?.path || typeof input.path !== "string")) {
        return "Either `artifactId` or `path` is required";
      }
      if (input.target !== "chat" && input.target !== "kb" && input.target !== "both") {
        return "`target` must be chat, kb, or both";
      }
      return null;
    },
    async execute(input, ctx): Promise<ToolResult<FileDeliverOutput>> {
      const start = Date.now();
      try {
        let artifact: OutputArtifactRecord;
        let bytes: Uint8Array;
        let trackRegistry = true;

        if (input.path && typeof input.path === "string") {
          // Direct workspace file delivery (no artifact registration needed)
          const resolved = path.resolve(deps.workspaceRoot, input.path);
          const rel = path.relative(deps.workspaceRoot, resolved);
          if (rel.startsWith("..") || path.isAbsolute(rel)) {
            return errorResult(new Error("Path outside workspace"), start);
          }
          bytes = await fs.readFile(resolved);
          const filename = path.basename(resolved);
          const ext = path.extname(filename).toLowerCase().slice(1);
          const mimeMap: Record<string, string> = {
            pdf: "application/pdf",
            xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            xls: "application/vnd.ms-excel",
            docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            hwpx: "application/hwp+zip",
            hwp: "application/x-hwp",
            csv: "text/csv",
            tsv: "text/tab-separated-values",
            txt: "text/plain",
            md: "text/markdown",
            html: "text/html",
            json: "application/json",
            png: "image/png",
            jpg: "image/jpeg",
            jpeg: "image/jpeg",
            gif: "image/gif",
            webp: "image/webp",
            zip: "application/zip",
          };
          const mimeType = mimeMap[ext] || "application/octet-stream";
          // Ad-hoc record for delivery (not registered in output registry)
          artifact = {
            artifactId: `path:${input.path}`,
            kind: "file",
            format: ext || "bin",
            title: filename,
            filename,
            mimeType,
            workspacePath: input.path,
            previewKind: "none",
          } as OutputArtifactRecord;
          trackRegistry = false;
        } else {
          artifact = await deps.outputRegistry.get(input.artifactId!);
          bytes = await readArtifactBytes(deps.workspaceRoot, artifact);
        }

        const filePath = path.resolve(deps.workspaceRoot, artifact.workspacePath);
        const rel = path.relative(deps.workspaceRoot, filePath);
        if (rel.startsWith("..") || path.isAbsolute(rel)) {
          return errorResult(new Error("Path outside workspace"), start);
        }

        const deliveries: FileDeliverOutput["deliveries"] = [];
        for (const target of toTargets(input.target)) {
          deliveries.push(await deliverWithRetry(target, deps, artifact, bytes, input, ctx, trackRegistry, filePath));
        }

        return {
          status: "ok",
          output: { deliveries },
          durationMs: Date.now() - start,
        };
      } catch (error) {
        return errorResult(error, start);
      }
    },
  };
}
