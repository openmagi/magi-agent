"use client";

import { useCallback, useState } from "react";
import { ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAgentFetch } from "@/lib/local-api";
import { decideApproval, usePendingApprovals } from "@/lib/credentials-api";
import type { ApprovalDecision } from "@/lib/credentials-api";

/**
 * "Pending approvals" queue for guarded credentials.
 *
 * When a credential is marked `requires_approval`, the (future) vault enqueues a
 * human-approval request before the agent may use it. The local operator
 * approves or denies it here. Records are metadata only — never a secret.
 */
export function PendingApprovals(): React.JSX.Element | null {
  const { data, loading, error, reload } = usePendingApprovals();
  const agentFetch = useAgentFetch();

  const [deciding, setDeciding] = useState<Set<string>>(new Set());
  const [decideError, setDecideError] = useState<string | null>(null);

  const handleDecide = useCallback(
    (id: string, decision: ApprovalDecision) => {
      setDeciding((prev) => new Set(prev).add(id));
      setDecideError(null);
      decideApproval(agentFetch, id, decision)
        .then(() => reload())
        .catch((err: unknown) => {
          setDecideError(
            err instanceof Error ? err.message : "Failed to record decision",
          );
        })
        .finally(() => {
          setDeciding((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        });
    },
    [agentFetch, reload],
  );

  // Hide the section entirely when there is nothing pending and no error — it is
  // a transient queue, not a permanent list.
  if (!loading && !error && (!data || data.approvals.length === 0)) {
    return null;
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <ShieldQuestion className="h-4 w-4 text-amber-600" />
        <h2 className="text-sm font-semibold text-foreground">
          Pending approvals
        </h2>
      </div>
      <p className="max-w-2xl text-xs leading-5 text-secondary">
        The agent has requested to use a guarded credential. Approve or deny each
        request below.
      </p>

      {loading ? (
        <div className="grid grid-cols-1 gap-3">
          <div className="h-16 animate-pulse rounded-2xl border border-black/[0.06] bg-gray-50" />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-4 text-sm leading-6 text-amber-800">
          <div className="font-semibold">Could not load the approval queue.</div>
          <div className="mt-1">{error}</div>
          <button
            type="button"
            onClick={reload}
            className="mt-3 inline-flex min-h-[40px] items-center rounded-lg border border-amber-500/30 bg-white px-4 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-50"
          >
            Retry
          </button>
        </div>
      ) : (
        <ul className="space-y-2">
          {data?.approvals.map((approval) => (
            <li
              key={approval.id}
              className="flex items-center justify-between gap-4 rounded-2xl border border-amber-500/30 bg-amber-500/[0.04] px-5 py-4"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {approval.requested_action} · {approval.target_host}
                </div>
                <div className="mt-1 text-xs text-secondary">
                  credential {approval.credential_id}
                  {approval.reason ? ` — ${approval.reason}` : ""}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  onClick={() => handleDecide(approval.id, "approved")}
                  disabled={deciding.has(approval.id)}
                >
                  {deciding.has(approval.id) ? "…" : "Approve"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => handleDecide(approval.id, "denied")}
                  disabled={deciding.has(approval.id)}
                >
                  Deny
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {decideError ? (
        <div className="rounded-lg border border-red-500/20 bg-red-500/[0.06] px-3 py-2 text-sm text-red-700">
          {decideError}
        </div>
      ) : null}
    </section>
  );
}
