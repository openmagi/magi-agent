import { getAuthUserFromHeader } from "@/lib/privy/server-auth";
import { isAdmin } from "@/lib/admin/guard";
import { AppError } from "@/lib/errors";

export interface AdminContext {
  auth: { userId: string };
  params: Record<string, string>;
}

type AdminHandler = (
  request: Request,
  ctx: AdminContext
) => Promise<Response>;

type RouteHandler = (
  request: Request,
  context: { params: Promise<Record<string, string>> }
) => Promise<Response>;

/**
 * Wraps a handler to require admin authentication.
 * Throws AppError(401) for missing auth, AppError(403) for non-admin users.
 */
export function withAdmin(handler: AdminHandler): RouteHandler {
  return async (request, context) => {
    const auth = await getAuthUserFromHeader(request);
    if (!auth) {
      throw new AppError("Unauthorized", 401);
    }
    if (!isAdmin(auth.userId)) {
      throw new AppError("Forbidden", 403);
    }
    const params = await context.params;
    return handler(request, { auth, params });
  };
}
