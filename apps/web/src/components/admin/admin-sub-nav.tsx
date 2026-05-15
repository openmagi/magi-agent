"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { label: "Users", href: "/dashboard/admin" },
  { label: "Analytics", href: "/dashboard/admin/analytics" },
  { label: "Skills", href: "/dashboard/admin/skills" },
] as const;

export function AdminSubNav() {
  const pathname = usePathname();

  return (
    <div className="flex items-center gap-1 mb-6">
      {TABS.map((tab) => {
        const isActive = tab.href === "/dashboard/admin"
          ? pathname === "/dashboard/admin"
          : pathname.startsWith(tab.href);

        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
              isActive
                ? "bg-primary/10 text-primary-light border border-primary/20"
                : "text-secondary hover:text-foreground hover:bg-black/[0.04]"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </div>
  );
}
