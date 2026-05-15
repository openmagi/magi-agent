"use client";

import { usePrivy } from "@privy-io/react-auth";
import { useCallback } from "react";

export function useAuthFetch() {
  const { getAccessToken } = usePrivy();

  return useCallback(async (url: string, options?: RequestInit) => {
    const token = await getAccessToken();
    return fetch(url, {
      ...options,
      headers: {
        ...options?.headers,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    });
  }, [getAccessToken]);
}
