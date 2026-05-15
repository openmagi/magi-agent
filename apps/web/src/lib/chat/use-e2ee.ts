"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { usePrivy, useWallets } from "@privy-io/react-auth";
import {
  deriveKey,
  deriveLegacyKey,
  deriveLegacySignedKey,
  deriveLegacyV2Key,
  encryptMessage,
  decryptMessage,
  E2EE_SIGN_MESSAGE,
} from "./e2ee";
import { decodeHistoryPlaintext, encodeHistoryPlaintext } from "./history-envelope";
import type { ChatMessage, ResearchEvidenceSnapshot, ResponseUsage } from "./types";

interface E2EEApiMessage {
  id: string;
  channel_name: string;
  role: "user" | "assistant";
  encrypted_content: string;
  iv: string;
  created_at: string;
  client_msg_id: string | null;
}

interface LoadMessagesOptions {
  latest?: boolean;
  before?: string;
}

interface LoadMessagesResult {
  messages: ChatMessage[];
  deletions: { client_msg_id: string | null; deleted_at: string }[];
  hasMore: boolean;
  nextBefore: string | null;
}

/**
 * E2EE hook — web version.
 * Signs with the embedded wallet and caches the signature per device.
 * Deterministic wallet signatures let other signed-in devices derive the same key.
 */
export function useE2EE(botId: string | null) {
  const { user, getAccessToken } = usePrivy();
  const { wallets } = useWallets();
  const [cryptoKey, setCryptoKey] = useState<CryptoKey | null>(null);
  const [legacyKeys, setLegacyKeys] = useState<CryptoKey[]>([]);
  const [ready, setReady] = useState(false);
  const keyDerivationAttempted = useRef(false);

  useEffect(() => {
    if (!user?.id || !wallets.length || keyDerivationAttempted.current) return;

    const embeddedWallet = wallets.find((w) => w.walletClientType === "privy");
    if (!embeddedWallet) return;

    keyDerivationAttempted.current = true;

    const legacyWebV1CacheKey = `clawy-e2ee-sig:${user.id}`;
    const cacheKeyV1 = `clawy-e2ee-sig:v1:${user.id}`;
    const cacheKeyV2Short = `clawy-e2ee-sig:v2-short:${user.id}`;
    const suspectV2CacheKey = `clawy-e2ee-sig:v2:${user.id}`;
    const cacheKeyV3 = `openmagi-e2ee-sig:v3:${user.id}`;

    (async () => {
      try {
        const signMessage = async (message: string): Promise<string> => {
          const provider = await embeddedWallet.getEthereumProvider();
          return (await provider.request({
            method: "personal_sign",
            params: [message, embeddedWallet.address],
          })) as string;
        };

        let signatureV3 = localStorage.getItem(cacheKeyV3);
        if (!signatureV3) {
          signatureV3 = await signMessage(E2EE_SIGN_MESSAGE);
          localStorage.setItem(cacheKeyV3, signatureV3);
        }

        const signatureV1 =
          localStorage.getItem(cacheKeyV1) ??
          localStorage.getItem(legacyWebV1CacheKey);

        const legacyKeyPromises: Promise<CryptoKey>[] = [];
        const legacyV2Signatures = new Set<string>();
        const cachedV2Short = localStorage.getItem(cacheKeyV2Short);
        const suspectV2Signature = localStorage.getItem(suspectV2CacheKey);
        if (cachedV2Short) legacyV2Signatures.add(cachedV2Short);
        if (suspectV2Signature) legacyV2Signatures.add(suspectV2Signature);
        if (signatureV1) {
          legacyV2Signatures.add(signatureV1);
          legacyKeyPromises.push(deriveLegacySignedKey(signatureV1, user.id));
        }
        for (const signature of legacyV2Signatures) {
          if (signature !== signatureV3) {
            legacyKeyPromises.push(deriveLegacyV2Key(signature, user.id));
          }
        }
        legacyKeyPromises.push(deriveLegacyKey(embeddedWallet.address, user.id));

        const [newKey, oldKeys] = await Promise.all([
          deriveKey(signatureV3, user.id),
          Promise.all(legacyKeyPromises),
        ]);

        setCryptoKey(newKey);
        setLegacyKeys(oldKeys);
        setReady(true);
      } catch {
        setReady(false);
      }
    })();
  }, [user?.id, wallets]);

  const saveMessages = useCallback(
    async (
      channelName: string,
      messages: {
        role: "user" | "assistant";
        content: string;
        clientMsgId: string;
        thinkingContent?: string;
        thinkingDuration?: number;
        researchEvidence?: ResearchEvidenceSnapshot;
        usage?: ResponseUsage;
      }[],
    ): Promise<void> => {
      if (!cryptoKey || !botId) return;

      const token = await getAccessToken();
      const encrypted = await Promise.all(
        messages.map(async (msg) => {
          const plaintext = encodeHistoryPlaintext(msg);
          const { encrypted: enc, iv } = await encryptMessage(cryptoKey, plaintext);
          return { role: msg.role, encrypted_content: enc, iv, client_msg_id: msg.clientMsgId };
        }),
      );

      await fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ botId, channelName, messages: encrypted }),
      });
    },
    [cryptoKey, botId, getAccessToken],
  );

  const deleteMessages = useCallback(
    async (
      channelName: string,
      messageIds: string[],
      deleteAll = false,
    ): Promise<boolean> => {
      if (!botId) return false;

      const token = await getAccessToken();
      const res = await fetch("/api/chat/messages", {
        method: "DELETE",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ botId, channelName, messageIds, deleteAll }),
      });
      return res.ok;
    },
    [botId, getAccessToken],
  );

  const loadMessages = useCallback(
    async (channelName: string, since?: string, limit = 100, options?: LoadMessagesOptions): Promise<LoadMessagesResult> => {
      if (!cryptoKey || !botId) return { messages: [], deletions: [], hasMore: false, nextBefore: null };

      const token = await getAccessToken();
      const params = new URLSearchParams({ botId, channelName, limit: String(limit) });
      if (since) params.set("since", since);
      if (options?.latest) params.set("latest", "true");
      if (options?.before) params.set("before", options.before);

      const res = await fetch(`/api/chat/messages?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return { messages: [], deletions: [], hasMore: false, nextBefore: null };

      const { messages: rows, deletions = [], hasMore = false, nextBefore = null } = (await res.json()) as {
        messages: E2EEApiMessage[];
        deletions?: { client_msg_id: string | null; deleted_at: string }[];
        hasMore?: boolean;
        nextBefore?: string | null;
      };
      console.log(`[e2ee] loadMessages: ${rows.length} rows from API for ${channelName}`);

      const keysToTry: CryptoKey[] = [cryptoKey, ...legacyKeys];

      let decryptOk = 0;
      let decryptFail = 0;
      const decrypted = await Promise.all(
        rows.map(async (row) => {
          for (const key of keysToTry) {
            try {
              const raw = await decryptMessage(key, row.encrypted_content, row.iv);
              decryptOk++;
              const decoded = decodeHistoryPlaintext(row.role, raw);
              return {
                id: row.client_msg_id ?? row.id,
                role: row.role,
                content: decoded.content,
                timestamp: new Date(row.created_at).getTime(),
                serverId: row.id,
                thinkingContent: decoded.thinkingContent,
                thinkingDuration: decoded.thinkingDuration,
                researchEvidence: decoded.researchEvidence,
                usage: decoded.usage,
              } as ChatMessage;
            } catch (err) {
              console.warn(`[e2ee] decrypt failed for ${row.id}:`, err instanceof Error ? err.message : err);
            }
          }
          decryptFail++;
          return null;
        }),
      );

      console.log(`[e2ee] decrypt results: ${decryptOk} ok, ${decryptFail} failed`);
      return {
        messages: decrypted.filter((m): m is ChatMessage => m !== null),
        deletions,
        hasMore,
        nextBefore,
      };
    },
    [cryptoKey, legacyKeys, botId, getAccessToken],
  );

  return { ready, saveMessages, loadMessages, deleteMessages };
}
