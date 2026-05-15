import { createHmac, randomBytes } from "crypto";
import { safeCompare } from "@/lib/auth/safe-compare";

export type OAuthProvider =
  | "google"
  | "notion"
  | "twitter"
  | "meta"
  | "dropbox"
  | "discord";

type OAuthStateData = Record<string, unknown>;

interface OAuthStatePayload<TData extends OAuthStateData> {
  provider: OAuthProvider;
  userId: string;
  nonce: string;
  expiresAt: number;
  data: TData;
}

interface OAuthStateCookie {
  name: string;
  value: string;
  httpOnly: true;
  sameSite: "lax";
  secure: true;
  path: string;
  maxAge: number;
}

interface CreateOAuthStateCookieOptions<TData extends OAuthStateData> {
  provider: OAuthProvider;
  userId: string;
  ttlSeconds: number;
  data?: TData;
  secret?: string;
  now?: number;
}

interface ConsumeOAuthStateCookieOptions {
  provider: OAuthProvider;
  state: string;
  cookieHeader: string | null;
  secret?: string;
  now?: number;
}

const usedNonces = new Map<string, number>();

export function getOAuthStateCookieName(provider: OAuthProvider): string {
  return `clawy_oauth_${provider}`;
}

export function getOAuthStateCookiePath(provider: OAuthProvider): string {
  return `/api/integrations/${provider}/callback`;
}

export function createOAuthStateCookie<TData extends OAuthStateData = OAuthStateData>(
  options: CreateOAuthStateCookieOptions<TData>,
): { state: string; cookie: OAuthStateCookie } {
  const now = options.now ?? Date.now();
  const nonce = randomBytes(32).toString("base64url");
  const payload: OAuthStatePayload<TData> = {
    provider: options.provider,
    userId: options.userId,
    nonce,
    expiresAt: now + options.ttlSeconds * 1000,
    data: options.data ?? ({} as TData),
  };
  const encodedPayload = Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
  const value = `${encodedPayload}.${sign(encodedPayload, getSecret(options.provider, options.secret))}`;

  return {
    state: nonce,
    cookie: {
      name: getOAuthStateCookieName(options.provider),
      value,
      httpOnly: true,
      sameSite: "lax",
      secure: true,
      path: getOAuthStateCookiePath(options.provider),
      maxAge: options.ttlSeconds,
    },
  };
}

export function consumeOAuthStateCookie<TData extends OAuthStateData = OAuthStateData>(
  options: ConsumeOAuthStateCookieOptions,
): { userId: string; data: TData } | null {
  const cookieValue = getCookieValue(options.cookieHeader, getOAuthStateCookieName(options.provider));
  if (!cookieValue) return null;

  const payload = decodeCookieValue<TData>(
    cookieValue,
    getSecret(options.provider, options.secret),
  );
  if (!payload) return null;
  if (payload.provider !== options.provider) return null;
  if (payload.nonce !== options.state) return null;

  const now = options.now ?? Date.now();
  pruneUsedNonces(now);
  if (payload.expiresAt <= now) return null;

  const replayKey = `${payload.provider}:${payload.nonce}`;
  if (usedNonces.has(replayKey)) return null;
  usedNonces.set(replayKey, payload.expiresAt);

  return { userId: payload.userId, data: payload.data };
}

export function clearOAuthStateCookie(provider: OAuthProvider): OAuthStateCookie {
  return {
    name: getOAuthStateCookieName(provider),
    value: "",
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: getOAuthStateCookiePath(provider),
    maxAge: 0,
  };
}

export function resetOAuthStateForTests(): void {
  usedNonces.clear();
}

function decodeCookieValue<TData extends OAuthStateData>(
  value: string,
  secret: string,
): OAuthStatePayload<TData> | null {
  const [encodedPayload, signature] = value.split(".");
  if (!encodedPayload || !signature) return null;

  const expected = sign(encodedPayload, secret);
  if (!safeCompare(signature, expected)) return null;

  try {
    return JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8")) as OAuthStatePayload<TData>;
  } catch {
    return null;
  }
}

function getCookieValue(cookieHeader: string | null, name: string): string | null {
  if (!cookieHeader) return null;

  for (const part of cookieHeader.split(";")) {
    const [rawName, ...rawValue] = part.trim().split("=");
    if (rawName === name) {
      return rawValue.join("=") || null;
    }
  }

  return null;
}

function sign(value: string, secret: string): string {
  return createHmac("sha256", secret).update(value).digest("base64url");
}

function getSecret(provider: OAuthProvider, override?: string): string {
  const secret = override
    ?? process.env.OAUTH_STATE_SECRET
    ?? providerSecret(provider);

  if (!secret) {
    throw new Error(`OAuth state secret is not configured for ${provider}`);
  }

  return secret;
}

function providerSecret(provider: OAuthProvider): string | undefined {
  switch (provider) {
    case "google":
      return process.env.GOOGLE_WS_CLIENT_SECRET;
    case "notion":
      return process.env.NOTION_CLIENT_SECRET;
    case "twitter":
      return process.env.TWITTER_CLIENT_SECRET;
    case "meta":
      return process.env.META_APP_SECRET;
    case "dropbox":
      return process.env.DROPBOX_APP_SECRET;
    case "discord":
      return process.env.DISCORD_CLIENT_SECRET;
  }
}

function pruneUsedNonces(now: number): void {
  for (const [key, expiresAt] of usedNonces.entries()) {
    if (expiresAt <= now) {
      usedNonces.delete(key);
    }
  }
}
