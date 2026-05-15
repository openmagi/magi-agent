/**
 * SIWA (Sign In With Agent) service.
 * Generates EIP-4361 messages and signs them using the bot's Privy wallet.
 */

import { signMessage, getWallet } from "@/lib/privy/wallet-service";
import { verifyMessage, type Hex } from "viem";
import { isIP } from "node:net";

export interface SiwaMessageInput {
  domain: string;
  uri: string;
  nonce: string;
  statement?: string;
  chainId?: number;
}

export interface SiwaSignResult {
  message: string;
  signature: string;
  address: string;
}

export interface SiwaVerifyResult {
  valid: boolean;
  address: string | null;
}

export interface SiwaPolicyOptions {
  now?: Date;
  maxAgeMs?: number;
  allowedDomains?: string[] | null;
}

export interface ParsedSiwaMessage extends SiwaMessageInput {
  address: string;
  issuedAt: Date;
}

const BASE_CHAIN_ID = 8453;
const DEFAULT_MAX_AGE_MS = 10 * 60 * 1000;
const NONCE_RE = /^[A-Za-z0-9._:-]{8,128}$/;
const HOSTNAME_RE = /^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])$/i;

function rejectControlChars(name: string, value: string): void {
  if (/[\r\n\0]/.test(value)) {
    throw new Error(`Invalid ${name}: control characters are not allowed`);
  }
}

function normalizedAllowedDomains(options?: SiwaPolicyOptions): Set<string> {
  const configured = options?.allowedDomains ??
    process.env.SIWA_ALLOWED_DOMAINS?.split(",") ??
    [];
  const allowed = new Set(
    configured
      .map((domain) => domain.trim().toLowerCase())
      .filter(Boolean),
  );
  if (allowed.size === 0) {
    throw new Error("SIWA_ALLOWED_DOMAINS must be configured before SIWA signing is enabled");
  }
  return allowed;
}

function validateDomain(domain: string): string {
  rejectControlChars("domain", domain);
  const normalized = domain.trim().toLowerCase().replace(/\.$/, "");
  if (
    normalized !== domain.toLowerCase() ||
    !HOSTNAME_RE.test(normalized) ||
    isIP(normalized) !== 0 ||
    normalized === "localhost" ||
    normalized.endsWith(".localhost") ||
    normalized.endsWith(".local") ||
    normalized.endsWith(".svc") ||
    normalized.endsWith(".svc.cluster.local") ||
    normalized.endsWith(".cluster.local")
  ) {
    throw new Error("Invalid SIWA hostname");
  }
  return normalized;
}

export function validateSiwaInput(input: SiwaMessageInput, options?: SiwaPolicyOptions): void {
  const domain = validateDomain(input.domain);
  rejectControlChars("uri", input.uri);
  rejectControlChars("nonce", input.nonce);
  if (input.statement) rejectControlChars("statement", input.statement);

  let uri: URL;
  try {
    uri = new URL(input.uri);
  } catch {
    throw new Error("Invalid SIWA URI");
  }
  if (uri.protocol !== "https:") {
    throw new Error("SIWA URI must use HTTPS");
  }
  if (uri.hostname.toLowerCase() !== domain) {
    throw new Error("SIWA domain and URI host must match");
  }
  if ((input.chainId ?? BASE_CHAIN_ID) !== BASE_CHAIN_ID) {
    throw new Error("SIWA chain ID must be Base");
  }
  if (!NONCE_RE.test(input.nonce)) {
    throw new Error("Invalid SIWA nonce");
  }

  const allowedDomains = normalizedAllowedDomains(options);
  if (allowedDomains.size > 0 && !allowedDomains.has(domain)) {
    throw new Error("SIWA domain is not allowed");
  }
}

function fieldValue(lines: string[], prefix: string): string | null {
  const line = lines.find((entry) => entry.startsWith(prefix));
  return line ? line.slice(prefix.length).trim() : null;
}

export function parseSiwaMessage(message: string, options?: SiwaPolicyOptions): ParsedSiwaMessage {
  const lines = message.split("\n");
  const firstLine = lines[0] ?? "";
  const domainMatch = firstLine.match(/^(.+) wants you to sign in with your Ethereum account:$/);
  if (!domainMatch) throw new Error("Invalid SIWA message domain line");

  const address = lines[1] ?? "";
  if (!/^0x[a-fA-F0-9]{40}$/.test(address)) {
    throw new Error("Invalid SIWA address");
  }

  const uri = fieldValue(lines, "URI:");
  const chainIdRaw = fieldValue(lines, "Chain ID:");
  const nonce = fieldValue(lines, "Nonce:");
  const issuedAtRaw = fieldValue(lines, "Issued At:");
  if (!uri || !chainIdRaw || !nonce || !issuedAtRaw) {
    throw new Error("Invalid SIWA message fields");
  }

  const chainId = Number(chainIdRaw);
  if (!Number.isInteger(chainId)) throw new Error("Invalid SIWA chain ID");

  const issuedAt = new Date(issuedAtRaw);
  if (Number.isNaN(issuedAt.getTime())) throw new Error("Invalid SIWA issued-at");

  const statementStart = lines[2] === "" ? 3 : 2;
  const uriLineIndex = lines.findIndex((entry) => entry.startsWith("URI:"));
  const statementLines = uriLineIndex > statementStart
    ? lines.slice(statementStart, uriLineIndex).filter((line) => line !== "")
    : [];
  const statement = statementLines.join(" ").trim() || undefined;

  const parsed: ParsedSiwaMessage = {
    domain: domainMatch[1],
    address,
    uri,
    nonce,
    statement,
    chainId,
    issuedAt,
  };
  validateSiwaInput(parsed, options);

  const now = options?.now ?? new Date();
  const maxAgeMs = options?.maxAgeMs ?? DEFAULT_MAX_AGE_MS;
  if (issuedAt.getTime() > now.getTime() + 60_000) {
    throw new Error("SIWA issued-at is in the future");
  }
  if (now.getTime() - issuedAt.getTime() > maxAgeMs) {
    throw new Error("SIWA message is expired");
  }

  return parsed;
}

/**
 * Build an EIP-4361 (Sign-In with Ethereum) message for an agent wallet.
 */
export function buildSiwaMessage(
  address: string,
  input: SiwaMessageInput,
): string {
  validateSiwaInput(input);
  const chainId = input.chainId ?? BASE_CHAIN_ID;
  const issuedAt = new Date().toISOString();
  const statement = input.statement || "Sign in with agent wallet";

  return [
    `${input.domain} wants you to sign in with your Ethereum account:`,
    address,
    "",
    statement,
    "",
    `URI: ${input.uri}`,
    `Version: 1`,
    `Chain ID: ${chainId}`,
    `Nonce: ${input.nonce}`,
    `Issued At: ${issuedAt}`,
  ].join("\n");
}

/**
 * Generate a SIWA message and sign it with the bot's Privy wallet.
 */
export async function signSiwaMessage(
  walletId: string,
  input: SiwaMessageInput,
): Promise<SiwaSignResult> {
  const wallet = await getWallet(walletId);
  const message = buildSiwaMessage(wallet.address, input);
  const signature = await signMessage(walletId, message);

  return {
    message,
    signature,
    address: wallet.address,
  };
}

/**
 * Verify a SIWA signature using viem's verifyMessage.
 */
export async function verifySiwaSignature(
  message: string,
  signature: string,
): Promise<SiwaVerifyResult> {
  try {
    const parsed = parseSiwaMessage(message);

    const valid = await verifyMessage({
      address: parsed.address as `0x${string}`,
      message,
      signature: signature as Hex,
    });

    return { valid, address: valid ? parsed.address : null };
  } catch {
    return { valid: false, address: null };
  }
}
