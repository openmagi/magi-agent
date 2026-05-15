import { getAuthUserFromHeader } from "@/lib/privy/server-auth";
import { ensureProfile } from "@/lib/privy/ensure-profile";
import * as Sentry from "@sentry/nextjs";
import { AppError } from "@/lib/errors";

export interface AuthContext {
  auth: { userId: string };
  params: Record<string, string>;
}

type AuthHandler = (
  request: Request,
  ctx: AuthContext
) => Promise<Response>;

type RouteHandler = (
  request: Request,
  context: { params: Promise<Record<string, string>> }
) => Promise<Response>;

/**
 * Wraps a handler to require authentication.
 * Throws AppError(401) if no valid auth token.
 * Injects `{ auth: { userId }, params }` into the handler context.
 */
export function withAuth(handler: AuthHandler): RouteHandler {
  return async (request, context) => {
    const auth = await getAuthUserFromHeader(request);
    if (!auth) {
      throw new AppError("Unauthorized", 401);
    }
    Sentry.setUser({ id: auth.userId });
    await ensureProfile(auth.userId);
    const params = await context.params;
    return handler(request, { auth, params });
  };
}
