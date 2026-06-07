import type { ChatMessage } from "./types";

type SaveUserHistoryMessage = (
  channelName: string,
  messages: {
    role: "user" | "assistant";
    content: string;
    clientMsgId: string;
  }[],
) => Promise<void>;

interface PersistUserHistoryMessageInput {
  e2eeReady: boolean;
  saveMessages: SaveUserHistoryMessage;
  channel: string;
  message: ChatMessage;
  content?: string;
  onError?: (err: unknown) => void;
  retryDelayMs?: number;
}

const USER_HISTORY_SAVE_RETRY_MS = 2_000;

export function persistUserHistoryMessage({
  e2eeReady,
  saveMessages,
  channel,
  message,
  content,
  onError,
  retryDelayMs = USER_HISTORY_SAVE_RETRY_MS,
}: PersistUserHistoryMessageInput): void {
  if (!e2eeReady || message.role !== "user") return;

  const payload = [{
    role: "user" as const,
    content: content ?? message.content,
    clientMsgId: message.id,
  }];

  saveMessages(channel, payload).catch(async () => {
    await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    saveMessages(channel, payload).catch((err) => {
      onError?.(err);
    });
  });
}
