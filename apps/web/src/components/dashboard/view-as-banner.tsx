"use client";

import Link from "next/link";
import { useViewAs } from "@/lib/admin/view-as-context";

export function ViewAsBanner(): React.ReactNode {
  const { viewAsUserId, viewAsDisplayName } = useViewAs();

  if (!viewAsUserId) return null;

  return (
    <div className="sticky top-0 z-40 p-3 bg-amber-500/10 border-b border-amber-500/20 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
        <span className="text-sm text-amber-300 font-medium">
          Viewing as: {viewAsDisplayName || viewAsUserId.slice(0, 30) + "..."}
        </span>
      </div>
      <Link
        href={`/dashboard/admin/users/${encodeURIComponent(viewAsUserId)}`}
        className="text-xs text-amber-300 hover:text-amber-200 transition-colors px-3 py-1 rounded-lg bg-amber-500/10 hover:bg-amber-500/20"
      >
        Exit View
      </Link>
    </div>
  );
}
