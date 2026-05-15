import { isAdmin } from "@/lib/admin/guard";

/**
 * For admin "view as user" — resolves the effective user ID from the request.
 * Checks both `viewAs` query param and `x-view-as-user-id` header.
 * If the caller is an admin, returns the viewed user's ID.
 * Otherwise returns the authenticated user's own ID.
 *
 * Only use this for READ operations. Mutations should always use auth.userId.
 */
export function resolveViewAsUserId(request: Request, authUserId: string): string {
  if (!isAdmin(authUserId)) return authUserId;

  // Check header first (set by useAuthFetch), then query param
  const headerViewAs = request.headers.get("x-view-as-user-id");
  if (headerViewAs) return headerViewAs;

  const url = new URL(request.url);
  const queryViewAs = url.searchParams.get("viewAs");
  if (queryViewAs) return decodeURIComponent(queryViewAs);

  return authUserId;
}
