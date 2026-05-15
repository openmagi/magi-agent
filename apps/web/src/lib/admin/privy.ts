import { PrivyClient } from "@privy-io/server-auth";
import { env } from "@/lib/config";

const privy = new PrivyClient(
  env.NEXT_PUBLIC_PRIVY_APP_ID,
  env.PRIVY_APP_SECRET,
);

interface PrivyUserInfo {
  email: string | null;
}

type PrivyLinkedAccount = {
  type: string;
  address?: string;
  email?: string;
  verifiedAt?: Date | null;
  firstVerifiedAt?: Date | null;
  latestVerifiedAt?: Date | null;
};

export async function getPrivyUsersBatch(
  userIds: string[],
): Promise<Map<string, PrivyUserInfo>> {
  const result = new Map<string, PrivyUserInfo>();

  // Privy SDK doesn't have a batch endpoint — fetch one by one
  // For beta with <10 users this is fine
  await Promise.all(
    userIds.map(async (userId) => {
      try {
        const user = await privy.getUser(userId);
        // Try email account first, then fall back to Google/Apple OAuth email
        let email: string | null = null;
        for (const a of user.linkedAccounts) {
          if (a.type === "email" && "address" in a) {
            email = a.address as string;
            break;
          }
          if ((a.type === "google_oauth" || a.type === "apple_oauth") && "email" in a && !email) {
            email = (a as unknown as { email: string }).email;
          }
        }
        result.set(userId, { email });
      } catch (err) {
        console.error(`[privy] Failed to fetch user ${userId}:`, err instanceof Error ? err.message : String(err));
        result.set(userId, { email: null });
      }
    }),
  );

  return result;
}

/**
 * Get a single user's email from Privy.
 * Returns null if user not found or no email linked.
 */
export async function getPrivyUserEmail(
  userId: string
): Promise<string | null> {
  try {
    const user = await privy.getUser(userId);
    for (const a of user.linkedAccounts) {
      if (a.type === "email" && "address" in a) {
        return a.address as string;
      }
      if (
        (a.type === "google_oauth" || a.type === "apple_oauth") &&
        "email" in a
      ) {
        return (a as unknown as { email: string }).email;
      }
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Return a Privy email only when the linked account carries verification
 * metadata. Invite acceptance is email-bound; bearer token possession alone is
 * not enough to join an organization.
 */
export async function getPrivyVerifiedUserEmail(
  userId: string,
): Promise<string | null> {
  try {
    const user = await privy.getUser(userId);
    for (const account of user.linkedAccounts as PrivyLinkedAccount[]) {
      if (account.type === "email" && account.address && isVerifiedAccount(account)) {
        return account.address;
      }
      if (
        (account.type === "google_oauth" || account.type === "apple_oauth") &&
        account.email &&
        isVerifiedAccount(account)
      ) {
        return account.email;
      }
    }
    return null;
  } catch {
    return null;
  }
}

function isVerifiedAccount(account: PrivyLinkedAccount): boolean {
  return Boolean(account.latestVerifiedAt || account.verifiedAt || account.firstVerifiedAt);
}
