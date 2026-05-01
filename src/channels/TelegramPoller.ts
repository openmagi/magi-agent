/**
 * TelegramPoller — native Telegram Bot API long-polling adapter.
 *
 * Replaces legacy gateway's `node-host` Telegram integration. Uses only
 * `fetch` + `FormData` (Node 22 built-ins); no telegraf / node-telegram-bot-api
 * dependency. Long-polls `getUpdates` with `timeout=25`, persists the
 * next offset to `{workspaceRoot}/.core-agent-state/telegram-offset.json`
 * via `atomicWriteJson` so a pod restart doesn't replay messages.
 *
 * Error handling — by design, the poll loop MUST survive transient
 * network / HTTP failures; a 502 or ECONNRESET should log a warning
 * and back off, not crash the adapter. Outbound `send()` calls
 * surface errors to the caller because the caller (Agent turn-end
 * handler) can decide whether to retry.
 *
 * Design reference: docs/plans/2026-04-19-core-agent-refactor-plan.md §2 C1.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { atomicWriteJson } from "../storage/atomicWrite.js";
import type {
  ChannelAdapter,
  InboundAttachment,
  InboundHandler,
  InboundMessage,
  OutboundMessage,
} from "./ChannelAdapter.js";

export interface TelegramPollerOptions {
  botToken: string;
  workspaceRoot: string;
  /** Injected for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /** getUpdates long-poll timeout in seconds. Default 25 (Telegram max is ~50). */
  longPollingTimeoutSec?: number;
  /** Backoff in ms between failed getUpdates attempts. Default 1000. */
  errorBackoffMs?: number;
}

interface TelegramUser {
  id: number;
  is_bot?: boolean;
  first_name?: string;
  username?: string;
}

interface TelegramChat {
  id: number;
  type?: string;
}

interface TelegramPhotoSize {
  file_id: string;
  file_unique_id: string;
  width: number;
  height: number;
  file_size?: number;
}

interface TelegramDocument {
  file_id: string;
  file_unique_id: string;
  file_name?: string;
  mime_type?: string;
  file_size?: number;
}

interface TelegramAudio {
  file_id: string;
  file_unique_id: string;
  mime_type?: string;
  file_size?: number;
  duration: number;
}

interface TelegramVideo {
  file_id: string;
  file_unique_id: string;
  mime_type?: string;
  file_size?: number;
  duration: number;
}

interface TelegramVoice {
  file_id: string;
  file_unique_id: string;
  mime_type?: string;
  file_size?: number;
  duration: number;
}

interface TelegramMessage {
  message_id: number;
  from?: TelegramUser;
  chat: TelegramChat;
  date: number;
  text?: string;
  /**
   * Native Telegram "Reply" — populated when the user taps Reply on
   * an earlier message. Lifted into InboundMessage.replyTo so the
   * Agent can inject a `[Reply to …]` preamble. We only read a small
   * subset of fields (message_id + text) — stickers / photos without
   * captions collapse to no preview and are skipped.
   *
   * Caption is the text of a media message (photo/document) — usable
   * as a preview fallback when `text` is absent but the user replied
   * to a captioned photo.
   */
  caption?: string;
  reply_to_message?: TelegramMessage;
  /** Photo array — multiple sizes; last element is highest resolution. */
  photo?: TelegramPhotoSize[];
  document?: TelegramDocument;
  audio?: TelegramAudio;
  video?: TelegramVideo;
  voice?: TelegramVoice;
}

interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
  edited_message?: TelegramMessage;
  channel_post?: TelegramMessage;
}

interface TelegramResponse<T> {
  ok: boolean;
  result?: T;
  description?: string;
  error_code?: number;
}

interface OffsetFile {
  offset: number;
}

const OFFSET_FILENAME = "telegram-offset.json";
const STATE_DIRNAME = ".core-agent-state";
const DOWNLOADS_DIRNAME = "telegram-downloads";
// Telegram sendMessage hard-limits text to 4096 characters. Keep a
// margin so Korean text, emojis, and future parse-mode changes do not
// turn a borderline chunk into a rejected one.
const TELEGRAM_TEXT_CHUNK_MAX_CHARS = 3500;

export class TelegramPoller implements ChannelAdapter {
  readonly kind = "telegram" as const;

  private readonly botToken: string;
  private readonly workspaceRoot: string;
  private readonly fetchImpl: typeof fetch;
  private readonly longPollingTimeoutSec: number;
  private readonly errorBackoffMs: number;

  private handler: InboundHandler | null = null;
  private offset = 0;
  private offsetLoaded = false;
  private abortController: AbortController | null = null;
  private running = false;
  private pollLoopPromise: Promise<void> | null = null;

  constructor(options: TelegramPollerOptions) {
    this.botToken = options.botToken;
    this.workspaceRoot = options.workspaceRoot;
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.longPollingTimeoutSec = options.longPollingTimeoutSec ?? 25;
    this.errorBackoffMs = options.errorBackoffMs ?? 1000;
  }

  onInboundMessage(handler: InboundHandler): void {
    this.handler = handler;
  }

  private offsetPath(): string {
    return path.join(this.workspaceRoot, STATE_DIRNAME, OFFSET_FILENAME);
  }

  private async loadOffsetIfNeeded(): Promise<void> {
    if (this.offsetLoaded) return;
    this.offsetLoaded = true;
    try {
      const raw = await fs.readFile(this.offsetPath(), "utf8");
      const parsed = JSON.parse(raw) as Partial<OffsetFile>;
      if (typeof parsed.offset === "number" && Number.isFinite(parsed.offset)) {
        this.offset = parsed.offset;
      }
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
        console.warn(
          `[telegram-poller] offset load failed: ${(err as Error).message}`,
        );
      }
    }
  }

  private async persistOffset(): Promise<void> {
    const data: OffsetFile = { offset: this.offset };
    try {
      await atomicWriteJson(this.offsetPath(), data);
    } catch (err) {
      console.warn(
        `[telegram-poller] offset persist failed: ${(err as Error).message}`,
      );
    }
  }

  private baseUrl(): string {
    return `https://api.telegram.org/bot${this.botToken}`;
  }

  /**
   * Fire one round of `getUpdates`, dispatch any inbound messages, and
   * persist the new offset. Exposed (not private) so tests can drive
   * the loop deterministically without involving real timers.
   */
  async pollOnce(): Promise<void> {
    await this.loadOffsetIfNeeded();
    const body = JSON.stringify({
      offset: this.offset,
      timeout: this.longPollingTimeoutSec,
      allowed_updates: ["message"],
    });
    let resp: Response;
    try {
      resp = await this.fetchImpl(`${this.baseUrl()}/getUpdates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        ...(this.abortController ? { signal: this.abortController.signal } : {}),
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") throw err;
      console.warn(
        `[telegram-poller] getUpdates network error: ${(err as Error).message}`,
      );
      return;
    }
    if (!resp.ok) {
      console.warn(`[telegram-poller] getUpdates HTTP ${resp.status}`);
      return;
    }
    let json: TelegramResponse<TelegramUpdate[]>;
    try {
      json = (await resp.json()) as TelegramResponse<TelegramUpdate[]>;
    } catch (err) {
      console.warn(
        `[telegram-poller] getUpdates JSON parse failed: ${(err as Error).message}`,
      );
      return;
    }
    if (!json.ok || !Array.isArray(json.result)) {
      console.warn(
        `[telegram-poller] getUpdates !ok: ${json.description ?? "unknown"}`,
      );
      return;
    }
    const updates = json.result;
    if (updates.length === 0) return;
    let maxUpdateId = this.offset - 1;
    for (const update of updates) {
      if (update.update_id > maxUpdateId) maxUpdateId = update.update_id;
      const inbound = convertUpdate(update);
      if (!inbound) continue;
      if (!this.handler) continue;

      // Download file attachment if present
      if (inbound._attachmentMeta) {
        const meta = inbound._attachmentMeta;
        const downloaded = await this.downloadFile(meta.fileId, meta.name);
        if (downloaded) {
          inbound.attachments = [{
            kind: meta.kind,
            name: meta.name,
            mimeType: downloaded.mimeType ?? meta.mimeType,
            localPath: downloaded.localPath,
            sizeBytes: meta.sizeBytes,
          }];
        }
        // Clean up internal field before passing to handler
        delete inbound._attachmentMeta;
      }

      try {
        await this.handler(inbound);
      } catch (err) {
        console.warn(
          `[telegram-poller] inbound handler threw: ${(err as Error).message}`,
        );
      }
    }
    this.offset = maxUpdateId + 1;
    await this.persistOffset();
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.abortController = new AbortController();
    // Codex P1 fix: kick off the long-poll loop without awaiting it so
    // Agent.start() can return and the HTTP server can boot. The loop
    // runs fire-and-forget; stop() awaits pollLoopPromise for clean exit.
    this.pollLoopPromise = this.runPollLoop();
  }

  private async runPollLoop(): Promise<void> {
    while (this.running) {
      try {
        await this.pollOnce();
      } catch (err) {
        if ((err as Error).name === "AbortError") break;
        console.warn(
          `[telegram-poller] pollOnce crashed: ${(err as Error).message}`,
        );
        await sleep(this.errorBackoffMs);
      }
    }
  }

  async stop(): Promise<void> {
    this.running = false;
    this.abortController?.abort();
    this.abortController = null;
    if (this.pollLoopPromise) {
      try {
        await this.pollLoopPromise;
      } catch {
        // swallow — loop body already logs errors
      }
      this.pollLoopPromise = null;
    }
  }

  async send(msg: OutboundMessage): Promise<void> {
    const chunks = splitTelegramText(msg.text);
    for (const [index, text] of chunks.entries()) {
      const body: Record<string, unknown> = {
        chat_id: msg.chatId,
        text,
      };
      if (index === 0 && msg.replyToMessageId) {
        const n = Number(msg.replyToMessageId);
        if (Number.isFinite(n)) body.reply_to_message_id = n;
      }
      const resp = await this.fetchImpl(`${this.baseUrl()}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = (await resp.json().catch(() => ({}))) as TelegramResponse<unknown>;
      if (!resp.ok || !json.ok) {
        throw new Error(
          `telegram sendMessage failed chunk ${index + 1}/${chunks.length}: ${json.description ?? `HTTP ${resp.status}`}`,
        );
      }
    }
  }

  /**
   * Download a file from Telegram by file_id. Calls getFile to get the
   * file_path, then downloads the binary from the Telegram file API.
   * Saves to `{workspaceRoot}/telegram-downloads/{filename}`.
   * Returns the local path on success, null on failure (non-throwing —
   * attachment download failure must never kill the user's turn).
   */
  async downloadFile(
    fileId: string,
    suggestedName?: string,
  ): Promise<{ localPath: string; mimeType?: string } | null> {
    try {
      // Step 1: getFile to obtain file_path
      const resp = await this.fetchImpl(`${this.baseUrl()}/getFile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: fileId }),
      });
      if (!resp.ok) {
        console.warn(`[telegram-poller] getFile HTTP ${resp.status} for ${fileId}`);
        return null;
      }
      const json = (await resp.json()) as TelegramResponse<{
        file_id: string;
        file_path?: string;
        file_size?: number;
      }>;
      if (!json.ok || !json.result?.file_path) {
        console.warn(`[telegram-poller] getFile no file_path for ${fileId}`);
        return null;
      }
      const filePath = json.result.file_path;

      // Step 2: Download the actual file
      const fileUrl = `https://api.telegram.org/file/bot${this.botToken}/${filePath}`;
      const fileResp = await this.fetchImpl(fileUrl);
      if (!fileResp.ok) {
        console.warn(`[telegram-poller] file download HTTP ${fileResp.status} for ${filePath}`);
        return null;
      }

      // Step 3: Save to workspace — sanitise filename to prevent path traversal
      const downloadsDir = path.join(this.workspaceRoot, DOWNLOADS_DIRNAME);
      await fs.mkdir(downloadsDir, { recursive: true });
      const rawName = suggestedName ?? path.basename(filePath);
      // Strip directory components and path traversal sequences
      const filename = path.basename(rawName).replace(/^\.+/, "_");
      const localPath = path.resolve(downloadsDir, filename);
      // Final guard: resolved path must stay under downloadsDir
      if (!localPath.startsWith(downloadsDir + path.sep) && localPath !== downloadsDir) {
        console.warn(`[telegram-poller] path traversal blocked: ${rawName}`);
        return null;
      }
      const buffer = Buffer.from(await fileResp.arrayBuffer());
      await fs.writeFile(localPath, buffer);

      // Infer mime type from Telegram's file_path extension
      const ext = path.extname(filePath).toLowerCase();
      const mimeMap: Record<string, string> = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
        ".html": "text/html", ".htm": "text/html", ".txt": "text/plain",
        ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
        ".mp4": "video/mp4", ".mov": "video/quicktime",
      };
      const mimeType = mimeMap[ext];
      return { localPath, mimeType };
    } catch (err) {
      console.warn(
        `[telegram-poller] downloadFile failed for ${fileId}: ${(err as Error).message}`,
      );
      return null;
    }
  }

  /**
   * POST to Telegram's `sendChatAction` endpoint with `action=typing`.
   *
   * Fire-and-forget — Telegram rate limit for `sendChatAction` is
   * generous (~one call / few seconds per chat); still, a 400/429/5xx
   * from the API must never bubble up and kill the user's turn.
   * Errors are logged at warn level and swallowed. Called on a 4s
   * cadence by {@link startTypingTicker} while a turn is generating.
   */
  async sendTyping(chatId: string): Promise<void> {
    try {
      const resp = await this.fetchImpl(`${this.baseUrl()}/sendChatAction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, action: "typing" }),
      });
      if (!resp.ok) {
        console.warn(
          `[telegram-poller] sendChatAction HTTP ${resp.status} chat=${chatId}`,
        );
        return;
      }
      // Drain body so the socket isn't left dangling on keep-alive
      // connections — but ignore parse errors.
      await resp.json().catch(() => ({}));
    } catch (err) {
      console.warn(
        `[telegram-poller] sendChatAction failed chat=${chatId}: ${(err as Error).message}`,
      );
    }
  }

  async sendDocument(
    chatId: string,
    filePath: string,
    caption?: string,
  ): Promise<void> {
    await this.sendFile("sendDocument", "document", chatId, filePath, caption);
  }

  async sendPhoto(
    chatId: string,
    filePath: string,
    caption?: string,
  ): Promise<void> {
    await this.sendFile("sendPhoto", "photo", chatId, filePath, caption);
  }

  private async sendFile(
    endpoint: "sendDocument" | "sendPhoto",
    field: "document" | "photo",
    chatId: string,
    filePath: string,
    caption: string | undefined,
  ): Promise<void> {
    const data = await fs.readFile(filePath);
    const form = new FormData();
    form.set("chat_id", chatId);
    if (caption) form.set("caption", caption);
    // Node 22 File constructor is available globally.
    const filename = path.basename(filePath);
    const blob = new Blob([new Uint8Array(data)]);
    form.set(field, blob, filename);
    const resp = await this.fetchImpl(`${this.baseUrl()}/${endpoint}`, {
      method: "POST",
      body: form,
    });
    const json = (await resp.json().catch(() => ({}))) as TelegramResponse<unknown>;
    if (!resp.ok || !json.ok) {
      throw new Error(
        `telegram ${endpoint} failed: ${json.description ?? `HTTP ${resp.status}`}`,
      );
    }
  }
}

/**
 * Extract the Telegram file attachment descriptor from a message, if any.
 * Returns null for pure-text messages or unsupported types (sticker, etc).
 */
function extractAttachmentMeta(
  message: TelegramMessage,
): { fileId: string; name: string; mimeType?: string; sizeBytes?: number; kind: "image" | "file" | "audio" } | null {
  if (message.document) {
    return {
      fileId: message.document.file_id,
      name: message.document.file_name ?? "document",
      mimeType: message.document.mime_type,
      sizeBytes: message.document.file_size,
      kind: "file",
    };
  }
  if (message.photo && message.photo.length > 0) {
    // Take highest resolution (last in array)
    const best = message.photo[message.photo.length - 1]!;
    return {
      fileId: best.file_id,
      name: "photo.jpg",
      sizeBytes: best.file_size,
      mimeType: "image/jpeg",
      kind: "image",
    };
  }
  if (message.audio) {
    return {
      fileId: message.audio.file_id,
      name: "audio",
      mimeType: message.audio.mime_type,
      sizeBytes: message.audio.file_size,
      kind: "audio",
    };
  }
  if (message.voice) {
    return {
      fileId: message.voice.file_id,
      name: "voice.ogg",
      mimeType: message.voice.mime_type ?? "audio/ogg",
      sizeBytes: message.voice.file_size,
      kind: "audio",
    };
  }
  if (message.video) {
    return {
      fileId: message.video.file_id,
      name: "video.mp4",
      mimeType: message.video.mime_type,
      sizeBytes: message.video.file_size,
      kind: "file",
    };
  }
  return null;
}

/**
 * Normalise a raw Telegram update → InboundMessage. Returns `null`
 * for updates we don't care about (edited_message, channel_post,
 * sticker-only message with no text/caption/attachment, etc) so the
 * caller can simply filter.
 *
 * Accepts messages with:
 *   - text (normal text message)
 *   - document / photo / audio / video / voice (file attachment)
 *   - caption (text accompanying a media message)
 *
 * When `message.reply_to_message` is populated (native Telegram Reply),
 * the quoted message is lifted into `replyTo` so the Agent can render
 * a `[Reply to user: …]` preamble. We conservatively stamp `role:
 * "user"` — the poller has no bot-own-message-id mapping so we can't
 * tell whether the quoted message was the bot's own reply without
 * tracking sent message ids (deferred).
 */
function convertUpdate(update: TelegramUpdate): (InboundMessage & { _attachmentMeta?: ReturnType<typeof extractAttachmentMeta> }) | null {
  const message = update.message;
  if (!message) return null;

  const hasText = typeof message.text === "string" && message.text.length > 0;
  const hasCaption = typeof message.caption === "string" && message.caption.length > 0;
  const attachmentMeta = extractAttachmentMeta(message);

  // Reject messages with no text, no caption, and no recognizable attachment
  if (!hasText && !hasCaption && !attachmentMeta) return null;

  // Use text > caption > placeholder for the message text
  const text = hasText
    ? message.text!
    : hasCaption
      ? message.caption!
      : "";

  const chatId = String(message.chat.id);
  const userId = message.from ? String(message.from.id) : chatId;
  const inbound: InboundMessage & { _attachmentMeta?: ReturnType<typeof extractAttachmentMeta> } = {
    channel: "telegram",
    chatId,
    userId,
    text,
    messageId: String(message.message_id),
    raw: update,
  };

  // Stash attachment metadata for the caller (pollOnce) to download
  if (attachmentMeta) {
    inbound._attachmentMeta = attachmentMeta;
  }

  const rtm = message.reply_to_message;
  if (rtm) {
    const preview =
      typeof rtm.text === "string" && rtm.text.length > 0
        ? rtm.text
        : typeof rtm.caption === "string" && rtm.caption.length > 0
          ? rtm.caption
          : "";
    // Skip empty-preview quotes (sticker-only replies etc). The
    // preamble is only useful when the model has *something* to
    // anchor on.
    if (preview.length > 0) {
      inbound.replyTo = {
        messageId: String(rtm.message_id),
        preview,
        role: "user",
      };
    }
  }
  return inbound;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function splitTelegramText(text: string): string[] {
  if (text.length <= TELEGRAM_TEXT_CHUNK_MAX_CHARS) return [text];

  const chunks: string[] = [];
  let remaining = text;
  while (remaining.length > TELEGRAM_TEXT_CHUNK_MAX_CHARS) {
    const hardSlice = safeSliceUtf16(remaining, TELEGRAM_TEXT_CHUNK_MAX_CHARS);
    const cut = findReadableCut(hardSlice) ?? hardSlice.length;
    chunks.push(remaining.slice(0, cut));
    remaining = remaining.slice(cut);
  }
  chunks.push(remaining);
  return chunks;
}

function findReadableCut(text: string): number | null {
  const minCut = Math.floor(TELEGRAM_TEXT_CHUNK_MAX_CHARS * 0.5);
  for (const separator of ["\n\n", "\n", ". ", " "]) {
    const index = text.lastIndexOf(separator);
    if (index >= minCut) return index + separator.length;
  }
  return null;
}

function safeSliceUtf16(text: string, maxChars: number): string {
  let slice = text.slice(0, maxChars);
  const lastCode = slice.charCodeAt(slice.length - 1);
  if (lastCode >= 0xd800 && lastCode <= 0xdbff) {
    slice = slice.slice(0, -1);
  }
  return slice;
}
