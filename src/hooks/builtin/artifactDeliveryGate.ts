/**
 * Artifact delivery gate.
 *
 * User-facing files and persistent artifacts should not be "created"
 * only inside the agent workspace and then disappear behind a
 * `fileRead:` hint. This gate turns file delivery into a native
 * beforeCommit invariant: generated deliverables need chat attachment
 * or KB-write evidence before the turn can close.
 */

import path from "node:path";
import type { HookContext, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { CompletionEvidenceAgent } from "./completionEvidenceGate.js";
import {
  getOrClassifyFinalAnswerMeta,
  getOrClassifyRequestMeta,
} from "./turnMetaClassifier.js";

const MAX_RETRIES = 1;

const ATTACHMENT_MARKER_RE = /\[attachment:[0-9a-f-]{36}:[^\]\r\n]+\]/i;

const USER_FACING_EXTENSIONS = new Set([
  ".md",
  ".txt",
  ".csv",
  ".tsv",
  ".json",
  ".html",
  ".pdf",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".xlsx",
  ".xls",
  ".docx",
  ".pptx",
  ".zip",
]);

const INTERNAL_PREFIXES = [
  ".git/",
  ".codex/",
  ".clawy/",
  "memory/",
  "sessions/",
  "skills/",
  "knowledge/",
  "workspace/knowledge/",
  "artifacts/index.json",
  "plans/",
];

const INTERNAL_BASENAMES = new Set([
  "AGENTS.md",
  "CLAUDE.md",
  "HEARTBEAT.md",
  "LEARNING.md",
  "MEMORY.md",
  "SOUL.md",
  "TOOLS.md",
  "SCRATCHPAD.md",
  "TASK-QUEUE.md",
  "WORKING.md",
  "agent.config.yaml",
  "agent.config.yml",
]);

const NATIVE_TOOL_CLAIM_DONE_RE =
  /(?:완료|했습니다|됐|되었습니다|생성|작성|저장|첨부|전달|delivered|sent|created|generated|saved|completed|done)/i;

const NATIVE_OUTPUT_TOOLS = [
  "DocumentWrite",
  "SpreadsheetWrite",
  "FileDeliver",
] as const;

interface DeliveryIntent {
  wantsAttachment: boolean;
  wantsKb: boolean;
  wantsFile: boolean;
}

interface NativeFileDelivery {
  target: "chat" | "kb";
  status?: string;
  marker?: string;
  externalId?: string;
}

export interface CreatedArtifact {
  kind: "file" | "artifact";
  name: string;
  path?: string;
  artifactId?: string;
  toolName: string;
}

export interface ArtifactDeliveryGateOptions {
  agent?: CompletionEvidenceAgent;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_ARTIFACT_DELIVERY_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function normalizeWorkspacePath(raw: string): string {
  return raw
    .trim()
    .replace(/^file:\/\//i, "")
    .replace(/^\/workspace\//, "")
    .replace(/^\.\//, "")
    .replace(/\\/g, "/");
}

function isInternalPath(rawPath: string): boolean {
  const normalized = normalizeWorkspacePath(rawPath);
  const withoutWorkspace = normalized.replace(/^workspace\//, "");
  if (INTERNAL_BASENAMES.has(path.posix.basename(normalized))) return true;
  return INTERNAL_PREFIXES.some(
    (prefix) => normalized.startsWith(prefix) || withoutWorkspace.startsWith(prefix),
  );
}

function isUserFacingFile(rawPath: string): boolean {
  if (!rawPath.trim()) return false;
  if (isInternalPath(rawPath)) return false;
  const ext = path.posix.extname(normalizeWorkspacePath(rawPath)).toLowerCase();
  return USER_FACING_EXTENSIONS.has(ext);
}

function isSuccessfulResult(entry: TranscriptEntry): boolean {
  if (entry.kind !== "tool_result") return false;
  if (entry.isError === true) return false;
  return !entry.status || entry.status === "ok" || entry.status === "success";
}

function successfulResultsById(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>> {
  const results = new Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>>();
  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (entry.kind !== "tool_result") continue;
    if (!isSuccessfulResult(entry)) continue;
    results.set(entry.toolUseId, entry);
  }
  return results;
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function parseOutputObject(output: string | undefined): Record<string, unknown> | null {
  if (!output) return null;
  try {
    const parsed = JSON.parse(output) as unknown;
    return objectRecord(parsed);
  } catch {
    return null;
  }
}

function stringField(source: Record<string, unknown> | null, key: string): string | null {
  const value = source?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function basenameForPath(rawPath: string): string {
  return path.posix.basename(normalizeWorkspacePath(rawPath));
}

function artifactFromFileTool(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: Extract<TranscriptEntry, { kind: "tool_result" }>,
): CreatedArtifact | null {
  const input = objectRecord(entry.input);
  const output = parseOutputObject(result.output);
  const rawPath = stringField(output, "path") ?? stringField(input, "path");
  if (!rawPath || !isUserFacingFile(rawPath)) return null;
  return {
    kind: "file",
    name: basenameForPath(rawPath),
    path: rawPath,
    toolName: entry.name,
  };
}

function artifactFromArtifactTool(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: Extract<TranscriptEntry, { kind: "tool_result" }>,
): CreatedArtifact | null {
  const input = objectRecord(entry.input);
  const output = parseOutputObject(result.output);
  const meta = objectRecord(output?.meta);
  const artifactId =
    stringField(output, "artifactId") ??
    stringField(input, "artifactId") ??
    stringField(meta, "artifactId");
  const artifactPath = stringField(meta, "path");
  const title = stringField(meta, "title") ?? stringField(input, "title");
  const name = artifactPath ? basenameForPath(artifactPath) : title ?? artifactId ?? "artifact";

  return {
    kind: "artifact",
    name,
    ...(artifactPath ? { path: artifactPath } : {}),
    ...(artifactId ? { artifactId } : {}),
    toolName: entry.name,
  };
}

function artifactFromOutputTool(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: Extract<TranscriptEntry, { kind: "tool_result" }>,
): CreatedArtifact | null {
  const input = objectRecord(entry.input);
  const output = parseOutputObject(result.output);
  const rawPath =
    stringField(output, "workspacePath") ??
    stringField(output, "path") ??
    stringField(input, "filename");
  const filename = stringField(output, "filename") ?? stringField(input, "filename");
  const artifactId = stringField(output, "artifactId");
  const title = stringField(input, "title");
  const name = filename ?? (rawPath ? basenameForPath(rawPath) : title ?? artifactId ?? "artifact");

  if (!isUserFacingFile(rawPath ?? name)) return null;

  return {
    kind: "artifact",
    name,
    ...(rawPath ? { path: rawPath } : {}),
    ...(artifactId ? { artifactId } : {}),
    toolName: entry.name,
  };
}

export function collectCreatedArtifacts(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): CreatedArtifact[] {
  const successful = successfulResultsById(transcript, turnId);
  if (successful.size === 0) return [];

  const artifacts: CreatedArtifact[] = [];
  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    const result = successful.get(entry.toolUseId);
    if (!result) continue;

    if (entry.name === "FileWrite" || entry.name === "FileEdit") {
      const artifact = artifactFromFileTool(entry, result);
      if (artifact) artifacts.push(artifact);
      continue;
    }

    if (entry.name === "ArtifactCreate" || entry.name === "ArtifactUpdate") {
      const artifact = artifactFromArtifactTool(entry, result);
      if (artifact) artifacts.push(artifact);
      continue;
    }

    if (entry.name === "DocumentWrite" || entry.name === "SpreadsheetWrite") {
      const artifact = artifactFromOutputTool(entry, result);
      if (artifact) artifacts.push(artifact);
    }
  }

  return artifacts;
}

function successfulToolNames(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Set<string> {
  const successful = successfulResultsById(transcript, turnId);
  const names = new Set<string>();
  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (successful.has(entry.toolUseId)) names.add(entry.name);
  }
  return names;
}

function nativeToolCompletionClaimsWithoutEvidence(
  assistantText: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string[] {
  if (!assistantText.trim()) return [];

  const successful = successfulToolNames(transcript, turnId);
  return NATIVE_OUTPUT_TOOLS.filter((toolName) => {
    const at = assistantText.indexOf(toolName);
    if (at < 0) return false;
    const window = assistantText.slice(Math.max(0, at - 80), at + toolName.length + 120);
    return NATIVE_TOOL_CLAIM_DONE_RE.test(window) && !successful.has(toolName);
  });
}

function nativeFileDeliveries(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): NativeFileDelivery[] {
  const successful = successfulResultsById(transcript, turnId);
  const deliveries: NativeFileDelivery[] = [];

  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (entry.name !== "FileDeliver") continue;

    const result = successful.get(entry.toolUseId);
    if (!result) continue;
    const output = parseOutputObject(result.output);
    const rawDeliveries = output?.deliveries;
    if (!Array.isArray(rawDeliveries)) continue;

    for (const raw of rawDeliveries) {
      const delivery = objectRecord(raw);
      const target = delivery?.target;
      if (target !== "chat" && target !== "kb") continue;
      deliveries.push({
        target,
        status: stringField(delivery, "status") ?? undefined,
        marker: stringField(delivery, "marker") ?? undefined,
        externalId: stringField(delivery, "externalId") ?? undefined,
      });
    }
  }

  return deliveries;
}

function nativeFileSendDeliveries(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): NativeFileDelivery[] {
  const successful = successfulResultsById(transcript, turnId);
  const deliveries: NativeFileDelivery[] = [];

  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (entry.name !== "FileSend") continue;

    const result = successful.get(entry.toolUseId);
    if (!result) continue;
    const output = parseOutputObject(result.output);
    const marker = stringField(output, "marker") ?? undefined;
    const channel = objectRecord(output?.channel);
    const channelType = stringField(channel, "type");
    const channelId = stringField(channel, "channelId");
    deliveries.push({
      target: "chat",
      status: "sent",
      marker,
      externalId: channelType && channelId ? `${channelType}:${channelId}` : stringField(output, "id") ?? undefined,
    });
  }

  return deliveries;
}

function sentNativeFileDeliveries(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): NativeFileDelivery[] {
  return nativeFileDeliveries(transcript, turnId).filter(
    (delivery) => delivery.status === "sent",
  );
}

function sentChatFileDeliveries(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): NativeFileDelivery[] {
  return [
    ...sentNativeFileDeliveries(transcript, turnId).filter((delivery) => delivery.target === "chat"),
    ...nativeFileSendDeliveries(transcript, turnId),
  ];
}

function sentNativeChatMarkers(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string[] {
  return sentChatFileDeliveries(transcript, turnId)
    .filter((delivery) => delivery.marker)
    .map((delivery) => delivery.marker as string);
}

function missingNativeChatMarkers(
  assistantText: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string[] {
  return sentNativeChatMarkers(transcript, turnId).filter(
    (marker) => !assistantText.includes(marker),
  );
}

function hasDirectChannelChatDelivery(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  return sentChatFileDeliveries(transcript, turnId).some(
    (delivery) =>
      !delivery.marker &&
      !!delivery.externalId &&
      /^(?:telegram|discord):/.test(delivery.externalId),
  );
}

function hasChatDeliveryEvidence(
  assistantText: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  const nativeMarkers = sentNativeChatMarkers(transcript, turnId);
  const hasRequiredNativeMarker =
    nativeMarkers.length === 0 || nativeMarkers.some((marker) => assistantText.includes(marker));
  return (
    (ATTACHMENT_MARKER_RE.test(assistantText) && hasRequiredNativeMarker) ||
    hasDirectChannelChatDelivery(transcript, turnId)
  );
}

function successfulBashCommands(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string[] {
  const successful = successfulResultsById(transcript, turnId);
  const commands: string[] = [];
  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (entry.name !== "Bash") continue;
    if (!successful.has(entry.toolUseId)) continue;
    const input = objectRecord(entry.input);
    const command = stringField(input, "command");
    if (command) commands.push(command);
  }
  return commands;
}

export function hasKbWriteEvidence(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  if (sentNativeFileDeliveries(transcript, turnId).some((delivery) => delivery.target === "kb")) {
    return true;
  }
  return successfulBashCommands(transcript, turnId).some((command) =>
    /(?:\bkb-write\.sh\b|knowledge-write\/(?:add|update|create-collection)|integration\.sh[\s\S]{0,120}knowledge-write)/i.test(
      command,
    ),
  );
}

export function hasArtifactDeliveryEvidence(
  assistantText: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  if (ATTACHMENT_MARKER_RE.test(assistantText)) return true;
  return hasKbWriteEvidence(transcript, turnId);
}

function hasRequiredDeliveryEvidence(
  intent: DeliveryIntent,
  assistantText: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  const hasAttachment = ATTACHMENT_MARKER_RE.test(assistantText);
  const nativeMarkers = sentNativeChatMarkers(transcript, turnId);
  const hasRequiredNativeMarker =
    nativeMarkers.length === 0 || nativeMarkers.some((marker) => assistantText.includes(marker));
  const hasDirectChannelDelivery = hasDirectChannelChatDelivery(transcript, turnId);
  const hasKbWrite = hasKbWriteEvidence(transcript, turnId);

  if (intent.wantsAttachment && ((!hasAttachment && !hasDirectChannelDelivery) || !hasRequiredNativeMarker)) return false;
  if (intent.wantsKb && !hasKbWrite) return false;
  if (intent.wantsAttachment || intent.wantsKb) return true;

  return (hasAttachment && hasRequiredNativeMarker) || hasDirectChannelDelivery || hasKbWrite;
}

function describeMissingEvidence(
  intent: DeliveryIntent,
  assistantText: string,
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string {
  const hasAttachment = ATTACHMENT_MARKER_RE.test(assistantText);
  const hasKbWrite = hasKbWriteEvidence(transcript, turnId);
  const missingMarkers = missingNativeChatMarkers(assistantText, transcript, turnId);
  const missing: string[] = [];

  if (intent.wantsAttachment && (!hasAttachment && !hasDirectChannelChatDelivery(transcript, turnId) || missingMarkers.length > 0)) {
    missing.push(
      missingMarkers.length > 0
        ? `returned FileDeliver marker in final answer: ${missingMarkers[0]}`
        : "chat attachment marker from file-send.sh/FileSend/FileDeliver, or direct Telegram/Discord delivery evidence",
    );
  }
  if (intent.wantsKb && !hasKbWrite) {
    missing.push("KB-write evidence from kb-write.sh, knowledge-write, or FileDeliver(target=\"kb\")");
  }
  if (!intent.wantsAttachment && !intent.wantsKb && !hasAttachment && !hasKbWrite) {
    missing.push("chat attachment marker or KB-write evidence");
  } else if (!intent.wantsAttachment && missingMarkers.length > 0) {
    missing.push(`returned FileDeliver marker in final answer: ${missingMarkers[0]}`);
  }

  return missing.join("; ");
}

function shouldGate(
  intent: DeliveryIntent,
  finalMeta: {
    assistantClaimsFileCreated: boolean;
    assistantClaimsChatDelivery: boolean;
    assistantClaimsKbDelivery: boolean;
  },
  artifacts: ReadonlyArray<CreatedArtifact>,
): boolean {
  if (artifacts.length === 0) return false;
  if (intent.wantsFile) return true;
  return (
    finalMeta.assistantClaimsFileCreated ||
    finalMeta.assistantClaimsChatDelivery ||
    finalMeta.assistantClaimsKbDelivery
  );
}

function formatArtifacts(artifacts: ReadonlyArray<CreatedArtifact>): string {
  return artifacts
    .slice(0, 5)
    .map((artifact) => {
      const location = artifact.path ?? artifact.artifactId ?? artifact.name;
      return `- ${artifact.name} (${artifact.toolName}: ${location})`;
    })
    .join("\n");
}

async function readTranscript(
  opts: ArtifactDeliveryGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[artifact-delivery-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

export function makeArtifactDeliveryGateHook(
  opts: ArtifactDeliveryGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:artifact-delivery-gate",
    point: "beforeCommit",
    priority: 89,
    blocking: true,
    timeoutMs: 8_000,
    handler: async ({ assistantText, userMessage, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };

        const transcript = await readTranscript(opts, ctx);
        const artifacts = collectCreatedArtifacts(transcript, ctx.turnId);
        const requestMeta = await getOrClassifyRequestMeta(ctx, { userMessage });
        const finalMeta = await getOrClassifyFinalAnswerMeta(ctx, {
          userMessage,
          assistantText,
        });
        const intent: DeliveryIntent = {
          wantsAttachment: requestMeta.fileDelivery.wantsChatDelivery,
          wantsKb: requestMeta.fileDelivery.wantsKbDelivery,
          wantsFile:
            requestMeta.fileDelivery.wantsFileOutput ||
            requestMeta.fileDelivery.wantsChatDelivery ||
            requestMeta.fileDelivery.wantsKbDelivery,
        };
        const unsupportedNativeClaims = nativeToolCompletionClaimsWithoutEvidence(
          assistantText,
          transcript,
          ctx.turnId,
        );
        if (unsupportedNativeClaims.length > 0) {
          ctx.emit({
            type: "rule_check",
            ruleId: "artifact-delivery-gate",
            verdict: "violation",
            detail: `native tool claim without successful tool result: ${unsupportedNativeClaims.join(", ")}`,
          });
          return {
            action: "block",
            reason: [
              "[RETRY:ARTIFACT_DELIVERY] The final answer claims native output tools completed,",
              "but the current turn transcript has no matching successful tool results.",
              "",
              `Missing tool evidence: ${unsupportedNativeClaims.join(", ")}`,
              "",
              "Before finalising, either call the native tool(s), or remove the success claim and state the actual status.",
            ].join("\n"),
          };
        }

        const missingDeliveredMarkers = missingNativeChatMarkers(assistantText, transcript, ctx.turnId);
        if (
          missingDeliveredMarkers.length > 0 &&
          (intent.wantsAttachment || finalMeta.assistantClaimsChatDelivery)
        ) {
          ctx.emit({
            type: "rule_check",
            ruleId: "artifact-delivery-gate",
            verdict: "violation",
            detail: "FileDeliver returned a chat marker that is missing from the final answer",
          });
          return {
            action: "block",
            reason: [
              "[RETRY:ARTIFACT_DELIVERY] FileDeliver uploaded a chat attachment,",
              "but the final answer does not include the returned attachment marker, so the client cannot render it.",
              "",
              `Include this exact marker in the final answer: ${missingDeliveredMarkers[0]}`,
            ].join("\n"),
          };
        }

        if (
          finalMeta.assistantClaimsChatDelivery &&
          !finalMeta.assistantReportsDeliveryFailure &&
          !hasChatDeliveryEvidence(assistantText, transcript, ctx.turnId)
        ) {
          ctx.emit({
            type: "rule_check",
            ruleId: "artifact-delivery-gate",
            verdict: "violation",
            detail: "file delivery claim without same-turn delivery evidence",
          });
          return {
            action: "block",
            reason: [
              "[RETRY:ARTIFACT_DELIVERY] The final answer claims a file/result was sent in chat,",
              "but the current turn has no successful chat delivery evidence.",
              "",
              "Before finalising, call `FileSend` or `FileDeliver(target=\"chat\")`. For web/app, include the returned attachment marker. For Telegram/Discord direct delivery, a successful native sendDocument/sendPhoto result is enough.",
              "If delivery is not possible, remove the delivery success claim and explicitly say it was not delivered.",
            ].join("\n"),
          };
        }

        if (!shouldGate(intent, finalMeta, artifacts)) {
          return { action: "continue" };
        }

        if (hasRequiredDeliveryEvidence(intent, assistantText, transcript, ctx.turnId)) {
          ctx.emit({
            type: "rule_check",
            ruleId: "artifact-delivery-gate",
            verdict: "ok",
            detail: "generated artifact has delivery evidence",
          });
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[artifact-delivery-gate] retry exhausted; failing open", {
            retryCount,
          });
          return { action: "continue" };
        }

        const missingEvidence = describeMissingEvidence(
          intent,
          assistantText,
          transcript,
          ctx.turnId,
        );
        const asksKb = intent.wantsKb ? " The user also asked for KB persistence." : "";
        ctx.emit({
          type: "rule_check",
          ruleId: "artifact-delivery-gate",
          verdict: "violation",
          detail: `generated artifact without delivery evidence; retryCount=${retryCount}`,
        });
        return {
          action: "block",
          reason: [
            "[RETRY:ARTIFACT_DELIVERY] This turn created a user-facing file/artifact,",
            `but the final answer is missing required delivery evidence: ${missingEvidence}.`,
            asksKb,
            "",
            "Created deliverables:",
            formatArtifacts(artifacts),
            "",
            "Before finalising:",
            "1) For web/app chat delivery, call `FileDeliver(target=\"chat\")` or run `file-send.sh <path> <channel>`, then include the exact returned `[attachment:<id>:<filename>]` marker in the reply.",
            "2) If the user asked for KB persistence, call `FileDeliver(target=\"kb\")`, run `kb-write.sh --add <collection> <filename> --stdin`, or use the knowledge-write integration before claiming it is saved.",
            "3) If delivery is temporarily unavailable after retrying, state that explicitly and provide the best available path/reference without claiming attachment or KB save.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[artifact-delivery-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const artifactDeliveryGateHook = makeArtifactDeliveryGateHook();
