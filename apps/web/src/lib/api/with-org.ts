import { getAuthUserFromHeader } from "@/lib/privy/server-auth";
import { createOrgClient } from "@/lib/supabase/org-client";
import { AppError } from "@/lib/errors";
import type { OrgRole } from "@/lib/supabase/types";

export interface OrgContext {
  auth: { userId: string };
  org: { id: string; name: string; slug: string; owner_id: string; credit_balance: number };
  role: OrgRole;
  params: Record<string, string>;
}

type OrgHandler = (
  request: Request,
  ctx: OrgContext
) => Promise<Response>;

type RouteHandler = (
  request: Request,
  context: { params: Promise<Record<string, string>> }
) => Promise<Response>;

/**
 * Wraps a handler to require auth + org membership.
 * Loads the org by `params.orgId` and verifies the authenticated user is a member.
 * Optionally requires admin role.
 */
export function withOrg(handler: OrgHandler, requireAdmin = false): RouteHandler {
  return async (request, context) => {
    const auth = await getAuthUserFromHeader(request);
    if (!auth) {
      throw new AppError("Unauthorized", 401);
    }

    const params = await context.params;
    const orgId = params.orgId;
    if (!orgId) {
      throw new AppError("Missing orgId parameter", 400);
    }

    const orgDb = createOrgClient();

    // Fetch org + membership in parallel
    const [orgResult, memberResult] = await Promise.all([
      orgDb
        .from("organizations")
        .select("id, name, slug, owner_id, credit_balance")
        .eq("id", orgId)
        .single(),
      orgDb
        .from("org_members")
        .select("role")
        .eq("org_id", orgId)
        .eq("user_id", auth.userId)
        .single(),
    ]);

    if (orgResult.error || !orgResult.data) {
      throw new AppError("Organization not found", 404);
    }

    if (memberResult.error || !memberResult.data) {
      throw new AppError("Not a member of this organization", 403);
    }

    const role = memberResult.data.role as OrgRole;

    if (requireAdmin && role !== "admin") {
      throw new AppError("Admin access required", 403);
    }

    return handler(request, {
      auth,
      org: orgResult.data,
      role,
      params,
    });
  };
}

/**
 * Shorthand for withOrg with admin requirement.
 */
export function withOrgAdmin(handler: OrgHandler): RouteHandler {
  return withOrg(handler, true);
}
