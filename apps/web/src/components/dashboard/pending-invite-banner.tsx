"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthFetch } from "@/lib/privy/use-auth-fetch";
import { Button } from "@/components/ui/button";
import type { ReactNode } from "react";

interface PendingInvite {
  id: string;
  token: string;
  email: string;
  expires_at: string;
  org: { id: string; name: string; slug: string } | null;
}

interface OrgConflict {
  hasConflict: boolean;
  currentOrg?: { id: string; name: string; slug: string };
  role?: string;
  isOwner?: boolean;
  transferTarget?: string | null;
  willArchive?: boolean;
}

export function PendingInviteBanner(): ReactNode {
  const authFetch = useAuthFetch();
  const router = useRouter();
  const [invites, setInvites] = useState<PendingInvite[]>([]);
  const [conflict, setConflict] = useState<OrgConflict | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);

  const fetchInvites = useCallback(async () => {
    try {
      const res = await authFetch("/api/invite/pending");
      if (!res.ok) return;
      const data = await res.json();
      setInvites(data.invites ?? []);

      if (data.invites?.length > 0) {
        const conflictRes = await authFetch("/api/invite/conflict");
        if (conflictRes.ok) {
          setConflict(await conflictRes.json());
        }
      }
    } catch {
      // silent
    }
  }, [authFetch]);

  useEffect(() => {
    void fetchInvites();
  }, [fetchInvites]);

  const handleAccept = async (invite: PendingInvite): Promise<void> => {
    if (conflict?.hasConflict && confirming !== invite.token) {
      setConfirming(invite.token);
      return;
    }

    setProcessing(true);
    try {
      const res = await authFetch(`/api/invite/${invite.token}`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const slug = data.org?.slug ?? invite.org?.slug;
        if (slug) {
          router.push(`/dashboard/org/${slug}`);
        } else {
          router.refresh();
        }
      }
    } catch {
      // silent
    }
    setProcessing(false);
    setConfirming(null);
  };

  const handleDecline = async (invite: PendingInvite): Promise<void> => {
    try {
      await authFetch(`/api/invite/${invite.token}`, { method: "DELETE" });
      setInvites((prev) => prev.filter((i) => i.id !== invite.id));
      setConfirming(null);
    } catch {
      // silent
    }
  };

  if (invites.length === 0) return null;

  return (
    <>
      {invites.map((invite) => (
        <div
          key={invite.id}
          className="sticky top-0 z-40 bg-blue-50 dark:bg-blue-950/50 border-b border-blue-200 dark:border-blue-800 px-4 py-3"
        >
          <div className="max-w-5xl mx-auto flex flex-col sm:flex-row items-start sm:items-center gap-2 sm:gap-4">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
                <span className="font-semibold">{invite.org?.name ?? "Organization"}</span>
                에서 초대가 왔습니다
              </p>

              {confirming === invite.token && conflict?.hasConflict && (
                <p className="text-xs text-blue-700 dark:text-blue-300 mt-1">
                  {getConflictWarning(conflict)}
                </p>
              )}
            </div>

            <div className="flex gap-2 shrink-0">
              {confirming === invite.token ? (
                <>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setConfirming(null)}
                    disabled={processing}
                  >
                    취소
                  </Button>
                  <Button
                    variant="cta"
                    size="sm"
                    onClick={() => handleAccept(invite)}
                    disabled={processing}
                  >
                    {processing ? "처리 중..." : "확인, 가입합니다"}
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleDecline(invite)}
                  >
                    거절
                  </Button>
                  <Button
                    variant="cta"
                    size="sm"
                    onClick={() => handleAccept(invite)}
                  >
                    수락
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      ))}
    </>
  );
}

function getConflictWarning(conflict: OrgConflict): string {
  const orgName = conflict.currentOrg?.name ?? "현재 조직";

  if (conflict.isOwner) {
    if (conflict.willArchive) {
      return `${orgName}의 소유자입니다. 수락하면 ${orgName}이(가) 보관 처리되고 모든 멤버가 탈퇴됩니다.`;
    }
    return `${orgName}의 소유자입니다. 수락하면 소유권이 다른 관리자에게 이전됩니다.`;
  }

  return `기존 ${orgName}에서 탈퇴하고 새 조직에 가입합니다.`;
}
