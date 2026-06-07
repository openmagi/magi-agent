"use client";

import { Suspense } from "react";
import { SidebarNav } from "@/components/dashboard/sidebar-nav";
import { MobileSidebar } from "@/components/dashboard/mobile-sidebar";

function LocalRuntimeHeader() {
  return (
    <div className="sticky top-0 z-20 border-b border-black/5 bg-white/85 backdrop-blur-xl">
      <div className="flex min-h-16 flex-col justify-center gap-1 px-4 py-3 sm:px-6 md:px-8">
        <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">
          Local Runtime
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-base font-semibold text-foreground">Magi Agent Console</h1>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Local
          </span>
        </div>
      </div>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(124,58,237,0.08),transparent_32rem),linear-gradient(180deg,#ffffff_0%,#f8fafc_42%,#fafafa_100%)]">
      <MobileSidebar />
      <div className="flex">
        <SidebarNav className="hidden md:flex" />
        <main className="flex-1 min-w-0">
          <LocalRuntimeHeader />
          <Suspense>
            <div className="min-w-0 px-4 py-5 sm:px-6 md:px-8 md:py-7">{children}</div>
          </Suspense>
        </main>
      </div>
    </div>
  );
}
