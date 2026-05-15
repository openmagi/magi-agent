"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { ViewAsProvider } from "@/lib/admin/view-as-context";
import { ViewAsBanner } from "@/components/dashboard/view-as-banner";
import { PolicyChangeBanner } from "@/components/dashboard/policy-change-banner";
import { PendingInviteBanner } from "@/components/dashboard/pending-invite-banner";
import type { ReactNode } from "react";

interface ViewAsWrapperProps {
  isAdmin: boolean;
  children: ReactNode;
}

export function ViewAsWrapper({ isAdmin, children }: ViewAsWrapperProps): ReactNode {
  const searchParams = useSearchParams();
  const rawViewAs = isAdmin ? searchParams.get("viewAs") : null;
  const viewAs = rawViewAs ? decodeURIComponent(rawViewAs) : null;
  const { getAccessToken } = usePrivy();
  const [displayName, setDisplayName] = useState<string | null>(null);

  useEffect(() => {
    if (!viewAs) return;
    let cancelled = false;

    async function fetchName(): Promise<void> {
      try {
        const token = await getAccessToken();
        const res = await fetch(`/api/admin/users/${encodeURIComponent(viewAs!)}/dashboard`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) {
          setDisplayName(data.profile?.displayName || data.profile?.email || null);
        }
      } catch {
        // ignore
      }
    }

    fetchName();
    return () => { cancelled = true; };
  }, [viewAs, getAccessToken]);

  return (
    <ViewAsProvider viewAsUserId={viewAs} viewAsDisplayName={displayName}>
      <ViewAsBanner />
      <PolicyChangeBanner />
      <PendingInviteBanner />
      {children}
    </ViewAsProvider>
  );
}
