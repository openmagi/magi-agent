import { PrivyClient } from "@privy-io/server-auth";
import { cookies } from "next/headers";
import { env } from "@/lib/config";

const privy = new PrivyClient(
  env.NEXT_PUBLIC_PRIVY_APP_ID,
  env.PRIVY_APP_SECRET
);

export async function getAuthUser(): Promise<{ userId: string } | null> {
  const cookieStore = await cookies();
  const token = cookieStore.get("privy-token")?.value;
  if (!token) return null;
  try {
    const { userId } = await privy.verifyAuthToken(token);
    return { userId };
  } catch {
    return null;
  }
}

/**
 * Returns true when the caller has a Privy refresh cookie but no valid
 * access token — used by server components to render a brief `AuthPending`
 * state instead of bouncing the user through a login redirect while the
 * client-side PrivyProvider silently refreshes. Checks the standard
 * Privy cookie layout (`privy-refresh-token`); absence is treated as
 * "no session at all, redirect normally".
 */
export async function hasRefreshToken(): Promise<boolean> {
  const cookieStore = await cookies();
  return Boolean(cookieStore.get("privy-refresh-token")?.value);
}

export async function getAuthUserFromHeader(request: Request): Promise<{ userId: string } | null> {
  const authHeader = request.headers.get("authorization");
  if (!authHeader?.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7);
  try {
    const { userId } = await privy.verifyAuthToken(token);
    return { userId };
  } catch {
    return null;
  }
}
