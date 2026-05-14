import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

export interface PublicFetchHostAddress {
  address: string;
  family: 4 | 6;
}

export type PublicFetchHostResolver = (hostname: string) => Promise<PublicFetchHostAddress[]>;

const BLOCKED_EXACT_HOSTS = new Set([
  "localhost",
  "metadata",
  "metadata.google.internal",
  "metadata.azure.com",
  "kubernetes.default",
  "kubernetes.default.svc",
  "kubernetes.default.svc.cluster.local",
]);

export async function defaultResolvePublicFetchHost(
  hostname: string,
): Promise<PublicFetchHostAddress[]> {
  const records = await lookup(hostname, { all: true, verbatim: false });
  return records.flatMap((record) => {
    if (record.family !== 4 && record.family !== 6) return [];
    return [{ address: record.address, family: record.family }];
  });
}

function normalizeHostname(hostname: string): string {
  return hostname.trim().toLowerCase().replace(/^\[/, "").replace(/\]$/, "").replace(/\.$/, "");
}

function isInternalHostname(hostname: string): boolean {
  const host = normalizeHostname(hostname);
  if (BLOCKED_EXACT_HOSTS.has(host)) return true;
  if (!host.includes(".") && isIP(host) === 0) return true;
  return (
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host.endsWith(".svc") ||
    host.endsWith(".svc.cluster.local") ||
    host.endsWith(".cluster.local")
  );
}

function parseIpv4(address: string): number[] | null {
  const parts = address.split(".");
  if (parts.length !== 4) return null;
  const octets = parts.map((part) => {
    if (!/^\d{1,3}$/.test(part)) return Number.NaN;
    const value = Number.parseInt(part, 10);
    return value >= 0 && value <= 255 ? value : Number.NaN;
  });
  return octets.every((octet) => Number.isInteger(octet)) ? octets : null;
}

function isBlockedIpv4(address: string): boolean {
  const octets = parseIpv4(address);
  if (!octets) return true;
  const a = octets[0] ?? -1;
  const b = octets[1] ?? -1;
  const c = octets[2] ?? -1;
  const d = octets[3] ?? -1;

  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 192 && b === 0 && c === 0) ||
    (a === 192 && b === 0 && c === 2) ||
    (a === 198 && (b === 18 || b === 19)) ||
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113) ||
    a >= 224 ||
    (a === 255 && b === 255 && c === 255 && d === 255)
  );
}

function isBlockedIpv6(address: string): boolean {
  const lower = address.toLowerCase();
  if (lower === "::" || lower === "::1") return true;
  if (lower.startsWith("::ffff:")) {
    const mapped = lower.slice("::ffff:".length);
    if (isIP(mapped) === 4) return isBlockedIpv4(mapped);
  }
  const first = Number.parseInt(lower.split(":")[0] || "0", 16);
  if (!Number.isFinite(first)) return true;
  return (
    (first & 0xfe00) === 0xfc00 ||
    (first & 0xffc0) === 0xfe80 ||
    (first & 0xff00) === 0xff00 ||
    lower.startsWith("2001:db8:")
  );
}

function isBlockedIpAddress(address: string): boolean {
  const normalized = normalizeHostname(address);
  const family = isIP(normalized);
  if (family === 4) return isBlockedIpv4(normalized);
  if (family === 6) return isBlockedIpv6(normalized);
  return true;
}

export async function validatePublicFetchUrl(
  raw: string,
  resolveHost: PublicFetchHostResolver | null,
): Promise<string | null> {
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return `unsupported fetch URL scheme: ${parsed.protocol}`;
    }
    const hostname = normalizeHostname(parsed.hostname);
    if (!hostname) return "fetch URL host is required";
    if (isInternalHostname(hostname)) return "fetch URL host must be a public internet host";
    const directIpFamily = isIP(hostname);
    if (directIpFamily !== 0) {
      return isBlockedIpAddress(hostname) ? "fetch URL IP must be public" : null;
    }
    if (!resolveHost) return null;

    let addresses: PublicFetchHostAddress[];
    try {
      addresses = await resolveHost(hostname);
    } catch {
      return "fetch URL host could not be resolved";
    }
    if (addresses.length === 0) return "fetch URL host did not resolve";
    if (addresses.some((address) => isBlockedIpAddress(address.address))) {
      return "fetch URL DNS result must be public";
    }
    return null;
  } catch {
    return "invalid fetch URL";
  }
}
