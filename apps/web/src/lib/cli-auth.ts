import { errors, jwtVerify, SignJWT } from "jose";
import { env } from "@/lib/config";

const ISSUER = "openmagi-cloud-cli";
const AUDIENCE = "openmagi-cli";
const DEFAULT_TTL_SECONDS = 8 * 60 * 60;

export type CloudCliTokenClaims = {
  userId: string;
  botId: string;
  scope: "cloud-cli";
};

export type MintCloudCliTokenInput = {
  userId: string;
  botId: string;
  now?: Date;
  ttlSeconds?: number;
};

export type VerifyCloudCliTokenOptions = {
  now?: Date;
};

function signingSecret(): Uint8Array {
  const raw = env.ENCRYPTION_KEY;
  if (/^[0-9a-fA-F]{64}$/.test(raw)) {
    return Uint8Array.from(Buffer.from(raw, "hex"));
  }
  return new TextEncoder().encode(raw);
}

function seconds(date: Date): number {
  return Math.floor(date.getTime() / 1000);
}

export async function mintCloudCliToken(input: MintCloudCliTokenInput): Promise<string> {
  const now = input.now ?? new Date();
  const issuedAt = seconds(now);
  const expiresAt = issuedAt + (input.ttlSeconds ?? DEFAULT_TTL_SECONDS);

  return new SignJWT({
    bot_id: input.botId,
    scope: "cloud-cli",
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuer(ISSUER)
    .setAudience(AUDIENCE)
    .setSubject(input.userId)
    .setIssuedAt(issuedAt)
    .setExpirationTime(expiresAt)
    .sign(signingSecret());
}

export async function verifyCloudCliToken(
  token: string,
  options: VerifyCloudCliTokenOptions = {},
): Promise<CloudCliTokenClaims> {
  try {
    const { payload } = await jwtVerify(token, signingSecret(), {
      issuer: ISSUER,
      audience: AUDIENCE,
      currentDate: options.now,
    });
    if (
      typeof payload.sub !== "string" ||
      typeof payload.bot_id !== "string" ||
      payload.scope !== "cloud-cli"
    ) {
      throw new Error("Invalid Cloud CLI token");
    }
    return {
      userId: payload.sub,
      botId: payload.bot_id,
      scope: "cloud-cli",
    };
  } catch (error) {
    if (error instanceof errors.JWTExpired) {
      throw new Error("Cloud CLI token expired");
    }
    if (error instanceof Error && error.message === "Invalid Cloud CLI token") {
      throw error;
    }
    throw new Error("Invalid Cloud CLI token");
  }
}

export function assertCloudCliBotAccess(claims: CloudCliTokenClaims, botId: string): void {
  if (claims.botId !== botId) {
    throw new Error("Cloud CLI token is not valid for this bot");
  }
}
