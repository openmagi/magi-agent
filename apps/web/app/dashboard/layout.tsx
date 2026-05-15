"use client";

import { Suspense } from "react";
import { SidebarNav } from "@/components/dashboard/sidebar-nav";
import { MobileSidebar } from "@/components/dashboard/mobile-sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <MobileSidebar />
      <div className="flex">
        <SidebarNav className="hidden md:flex" />
        <main className="flex-1 min-w-0">
          <Suspense>
            <div className="min-w-0 p-4 sm:p-6 md:p-8">{children}</div>
          </Suspense>
        </main>
      </div>
    </div>
  );
}
