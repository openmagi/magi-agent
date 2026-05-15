"use client";

import { Suspense } from "react";
import { SidebarNav } from "@/components/dashboard/sidebar-nav";
import { MobileSidebar } from "@/components/dashboard/mobile-sidebar";
import { ViewAsWrapper } from "@/components/dashboard/view-as-wrapper";
import { PLAN_MAX_BOTS } from "@/lib/billing/plans";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  // OSS: no server-side auth — sidebar renders with defaults
  const showAdmin = false;
  const bots: { id: string; name: string; status: string }[] = [];
  const maxBots = PLAN_MAX_BOTS["pro"] ?? 1;
  const org = null;

  return (
    <div className="min-h-screen bg-background">
      <MobileSidebar showAdmin={showAdmin} bots={bots} maxBots={maxBots} org={org} />
      <div className="flex">
        <SidebarNav showAdmin={showAdmin} bots={bots} maxBots={maxBots} org={org} className="hidden md:flex" />
        <main className="flex-1 min-w-0">
          <Suspense>
            <ViewAsWrapper isAdmin={showAdmin}>
              <div className="min-w-0 p-4 sm:p-6 md:p-8">{children}</div>
            </ViewAsWrapper>
          </Suspense>
        </main>
      </div>
    </div>
  );
}
