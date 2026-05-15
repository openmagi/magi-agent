"use client";

import { useAgentFetch } from "@/lib/local-api";

/**
 * OSS replacement for the cloud Privy-based useAuthFetch hook.
 * Returns agentFetch which talks to the local agent API.
 */
export function useAuthFetch(): (url: string, options?: RequestInit) => Promise<Response> {
  return useAgentFetch();
}
