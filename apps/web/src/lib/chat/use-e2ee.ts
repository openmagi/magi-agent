"use client";

import { useCallback } from "react";
import type { ChatMessage, ResearchEvidenceSnapshot, ResponseUsage } from "./types";

/**
 * OSS stub — E2EE is disabled in the open-source build.
 * Messages are stored and transmitted in plaintext.
 */

export interface E2EEHook {
  ready: boolean;
  encrypt: (content: string, thinkingContent?: string, thinkingDuration?: number | null) => Promise<string>;
  decrypt: (encrypted: string) => Promise<{ content: string; thinkingContent?: string; thinkingDuration?: number | null }>;
  decryptHistoryMessages: (raw: unknown[]) => Promise<ChatMessage[]>;
  saveMessages: (
    channel: string,
    messages: Array<{
      role: "user" | "assistant";
      content: string;
      clientMsgId?: string;
      thinkingContent?: string;
      thinkingDuration?: number | null;
      researchEvidence?: ResearchEvidenceSnapshot;
      usage?: ResponseUsage;
    }>,
  ) => Promise<void>;
  loadMessages: (
    channel: string,
    since?: string,
    limit?: number,
    options?: { latest?: boolean; before?: string | null },
  ) => Promise<{
    messages: ChatMessage[];
    deletions: Array<{ client_msg_id: string | null }>;
    hasMore: boolean;
    nextBefore: string | null;
  }>;
  deleteMessages: (
    channel: string,
    messageIds: string[],
    channelWide?: boolean,
  ) => Promise<void>;
}

export function useE2EE(_botId?: string): E2EEHook {
  const encrypt = useCallback(async (content: string, thinkingContent?: string, thinkingDuration?: number | null) => {
    // No encryption in OSS — return as JSON envelope for compatibility
    return JSON.stringify({ content, thinkingContent: thinkingContent ?? undefined, thinkingDuration: thinkingDuration ?? undefined });
  }, []);

  const decrypt = useCallback(async (data: string) => {
    try {
      const parsed = JSON.parse(data);
      return {
        content: parsed.content ?? data,
        thinkingContent: parsed.thinkingContent,
        thinkingDuration: parsed.thinkingDuration,
      };
    } catch {
      return { content: data };
    }
  }, []);

  const decryptHistoryMessages = useCallback(async (raw: unknown[]): Promise<ChatMessage[]> => {
    return raw.map((item) => {
      const msg = item as Record<string, unknown>;
      let content = (msg.encrypted_content ?? msg.content ?? "") as string;
      let thinkingContent: string | undefined;
      let thinkingDuration: number | null | undefined;
      try {
        const parsed = JSON.parse(content);
        if (parsed.content) {
          content = parsed.content;
          thinkingContent = parsed.thinkingContent;
          thinkingDuration = parsed.thinkingDuration;
        }
      } catch { /* plain text */ }
      return {
        id: msg.id as string,
        role: msg.role as "user" | "assistant",
        content,
        timestamp:
          typeof msg.timestamp === "number"
            ? msg.timestamp
            : typeof msg.created_at === "string"
              ? new Date(msg.created_at).getTime()
            : Date.now(),
        thinkingContent,
        thinkingDuration: thinkingDuration ?? undefined,
        serverId: msg.server_id as string | undefined,
        channel: msg.channel_name as string | undefined,
        created_at: msg.created_at as string | undefined,
        researchEvidence: msg.research_evidence as ResearchEvidenceSnapshot | undefined,
        usage: msg.usage as ResponseUsage | undefined,
      };
    });
  }, []);

  const saveMessages = useCallback(async () => {
    // The OSS chat store already persists local messages to localStorage.
  }, []);

  const loadMessages = useCallback(async () => ({
    messages: [],
    deletions: [],
    hasMore: false,
    nextBefore: null,
  }), []);

  const deleteMessages = useCallback(async () => {
    // Local deletion is handled by the chat store.
  }, []);

  return {
    ready: true,
    encrypt,
    decrypt,
    decryptHistoryMessages,
    saveMessages,
    loadMessages,
    deleteMessages,
  };
}
