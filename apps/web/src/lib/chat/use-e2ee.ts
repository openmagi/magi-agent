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
}

export function useE2EE(): E2EEHook {
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
        thinkingContent,
        thinkingDuration,
        serverId: msg.server_id as string | undefined,
        channel: msg.channel_name as string | undefined,
        created_at: msg.created_at as string | undefined,
        researchEvidence: msg.research_evidence as ResearchEvidenceSnapshot | undefined,
        usage: msg.usage as ResponseUsage | undefined,
      } as ChatMessage;
    });
  }, []);

  return { ready: true, encrypt, decrypt, decryptHistoryMessages };
}
