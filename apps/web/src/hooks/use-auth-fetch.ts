"use client";

import { useCallback } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { useViewAs } from "@/lib/admin/view-as-context";

export function useAuthFetch(): (url: string, options?: RequestInit) => Promise<Response> {
  const { getAccessToken } = usePrivy();
  const { viewAsUserId } = useViewAs();

  const authFetch = useCallback(
    async (url: string, options?: RequestInit): Promise<Response> => {
      const token = await getAccessToken();
      const headers: Record<string, string> = {
        ...options?.headers as Record<string, string>,
        Authorization: `Bearer ${token}`,
      };
      if (viewAsUserId) {
        headers["x-view-as-user-id"] = viewAsUserId;
      }
      return fetch(url, {
        ...options,
        headers,
      });
    },
    [getAccessToken, viewAsUserId]
  );

  return authFetch;
}
