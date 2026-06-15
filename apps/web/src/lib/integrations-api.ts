"use client";

import { useCallback, useEffect, useState } from "react";
import { useAgentFetch } from "./local-api";

/**
 * Types + data hooks for the local runtime "Integrations" admin surface.
 *
 * Mirrors the `/v1/admin/integrations` contract served by the local Python
 * runtime. Secrets (Composio API key, Telegram bot token) are write-only: sent
 * once, forwarded to the vault seam, never returned.
 */

export interface VaultStatus {
  present: boolean;
  healthy: boolean;
}

export interface ComposioStatus {
  configured: boolean;
}

export interface TelegramStatus {
  configured: boolean;
  label: string | null;
  easy_available?: boolean;
}

export interface IntegrationsStatus {
  composio: ComposioStatus;
  telegram: TelegramStatus;
  vault_status: VaultStatus;
}

export interface CatalogItem {
  slug: string;
  name: string;
  logo: string | null;
  categories: string[];
}

export interface CatalogPage {
  items: CatalogItem[];
  next_cursor: string | null;
}

export interface ConnectionItem {
  connection_id: string;
  toolkit: string;
  status: string;
}

export interface ConnectResult {
  connection_id: string;
  status: string;
  redirect_url: string | null;
}

export interface ConnectionStatusResult {
  connection_id: string;
  status: string;
  toolkit: string | null;
}

type AgentFetch = (path: string, init?: RequestInit) => Promise<Response>;

const BASE = "/v1/admin/integrations";

async function expectOk(res: Response, what: string): Promise<Response> {
  if (!res.ok) throw new Error(`${what} (${res.status})`);
  return res;
}

interface UseIntegrationsResult {
  data: IntegrationsStatus | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useIntegrations(): UseIntegrationsResult {
  const agentFetch = useAgentFetch();
  const [data, setData] = useState<IntegrationsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((v) => v + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    agentFetch(BASE)
      .then(async (res) => {
        await expectOk(res, "Failed to load integrations");
        const payload = (await res.json()) as IntegrationsStatus;
        if (!cancelled) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load integrations");
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentFetch, reloadKey]);

  return { data, loading, error, reload };
}

export async function setComposioKey(fetch: AgentFetch, apiKey: string): Promise<ComposioStatus> {
  const res = await fetch(`${BASE}/composio/key`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  await expectOk(res, "Failed to save Composio key");
  return ((await res.json()) as { composio: ComposioStatus }).composio;
}

export async function clearComposioKey(fetch: AgentFetch): Promise<ComposioStatus> {
  const res = await fetch(`${BASE}/composio/key`, { method: "DELETE" });
  await expectOk(res, "Failed to clear Composio key");
  return ((await res.json()) as { composio: ComposioStatus }).composio;
}

export async function fetchComposioCatalog(
  fetch: AgentFetch,
  opts: { category?: string; cursor?: string } = {},
): Promise<CatalogPage> {
  const params = new URLSearchParams();
  if (opts.category) params.set("category", opts.category);
  if (opts.cursor) params.set("cursor", opts.cursor);
  const query = params.toString();
  const res = await fetch(`${BASE}/composio/catalog${query ? `?${query}` : ""}`);
  await expectOk(res, "Failed to load catalog");
  return (await res.json()) as CatalogPage;
}

export async function composioConnect(fetch: AgentFetch, toolkit: string): Promise<ConnectResult> {
  const res = await fetch(`${BASE}/composio/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ toolkit }),
  });
  await expectOk(res, "Failed to start connection");
  return (await res.json()) as ConnectResult;
}

export async function composioConnectStatus(
  fetch: AgentFetch,
  connectionId: string,
): Promise<ConnectionStatusResult> {
  const res = await fetch(
    `${BASE}/composio/connect/${encodeURIComponent(connectionId)}/status`,
  );
  await expectOk(res, "Failed to poll connection");
  return (await res.json()) as ConnectionStatusResult;
}

export async function fetchComposioConnections(fetch: AgentFetch): Promise<ConnectionItem[]> {
  const res = await fetch(`${BASE}/composio/connections`);
  await expectOk(res, "Failed to load connections");
  return ((await res.json()) as { connections: ConnectionItem[] }).connections;
}

export async function composioDisconnect(fetch: AgentFetch, connectionId: string): Promise<void> {
  const res = await fetch(
    `${BASE}/composio/connection/${encodeURIComponent(connectionId)}`,
    { method: "DELETE" },
  );
  await expectOk(res, "Failed to disconnect");
}

export async function setTelegramToken(fetch: AgentFetch, token: string): Promise<TelegramStatus> {
  const res = await fetch(`${BASE}/telegram/token`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (res.status === 400) throw new Error("Telegram rejected that bot token.");
  await expectOk(res, "Failed to save Telegram token");
  return ((await res.json()) as { telegram: TelegramStatus }).telegram;
}

export async function clearTelegramToken(fetch: AgentFetch): Promise<TelegramStatus> {
  const res = await fetch(`${BASE}/telegram/token`, { method: "DELETE" });
  await expectOk(res, "Failed to clear Telegram token");
  return ((await res.json()) as { telegram: TelegramStatus }).telegram;
}

// --- Telegram easy setup (phone → BotFather), gated ---

export async function easySendCode(fetch: AgentFetch, phone: string): Promise<string> {
  const res = await fetch(`${BASE}/telegram/easy/send-code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone }),
  });
  await expectOk(res, "Failed to send code");
  return ((await res.json()) as { session_id: string }).session_id;
}

export async function easyVerifyCode(
  fetch: AgentFetch,
  sessionId: string,
  code: string,
): Promise<boolean> {
  const res = await fetch(`${BASE}/telegram/easy/verify-code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, code }),
  });
  await expectOk(res, "Failed to verify code");
  return ((await res.json()) as { needs_2fa: boolean }).needs_2fa;
}

export async function easyVerify2fa(
  fetch: AgentFetch,
  sessionId: string,
  password: string,
): Promise<void> {
  const res = await fetch(`${BASE}/telegram/easy/verify-2fa`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, password }),
  });
  await expectOk(res, "Failed to verify password");
}

export async function easyCreateBot(
  fetch: AgentFetch,
  sessionId: string,
  botName: string,
): Promise<TelegramStatus> {
  const res = await fetch(`${BASE}/telegram/easy/create-bot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, bot_name: botName }),
  });
  await expectOk(res, "Failed to create bot");
  return ((await res.json()) as { telegram: TelegramStatus }).telegram;
}
