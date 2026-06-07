"use client";

import type { ComponentType } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Brain,
  Gauge,
  LayoutDashboard,
  MessageSquare,
  Settings,
  Sparkles,
  Wrench,
} from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { LanguageSwitcher } from "@/components/ui/language-switcher";
import { useMessages } from "@/lib/i18n";

interface SidebarNavProps {
  onNavigate?: () => void;
  className?: string;
}

export function SidebarNav({ onNavigate, className }: SidebarNavProps) {
  const pathname = usePathname();
  const t = useMessages();

  const botId = "local";
  const botPrefix = `/dashboard/${botId}`;

  const botNavItems = [
    { href: `${botPrefix}/chat`, label: "Chat", prefix: `${botPrefix}/chat`, icon: MessageSquare },
    { href: `${botPrefix}/overview`, label: t.dashboard.overview, icon: LayoutDashboard },
    { href: `${botPrefix}/settings`, label: t.dashboard.settings, icon: Settings },
    { href: `${botPrefix}/customize`, label: t.customize?.tabCustomize ?? "Customize", icon: Wrench },
    { href: `${botPrefix}/usage`, label: t.dashboard.usage, icon: Gauge },
    { href: `${botPrefix}/skills`, label: t.dashboard.skills, icon: Sparkles },
    { href: `${botPrefix}/memory`, label: t.dashboard.memory, icon: Brain },
  ];

  function isActive(item: { href: string; prefix?: string }): boolean {
    const matchPath = item.prefix || item.href;
    return pathname.startsWith(matchPath);
  }

  function renderNavItem(item: {
    href: string;
    label: string;
    prefix?: string;
    icon: ComponentType<{ className?: string; strokeWidth?: number }>;
  }) {
    const active = isActive(item);
    const Icon = item.icon;
    return (
      <Link
        key={item.href}
        href={item.href}
        onClick={() => { onNavigate?.(); }}
        className={`group flex min-h-10 items-center gap-3 rounded-xl px-3 text-[13px] font-semibold transition-colors duration-200 cursor-pointer ${
          active
            ? "border border-primary/20 bg-primary/10 text-primary-light shadow-sm shadow-primary/5"
            : "border border-transparent text-gray-600 hover:border-black/[0.04] hover:bg-white hover:text-gray-950"
        }`}
      >
        <Icon
          className={`h-4 w-4 shrink-0 ${active ? "text-primary-light" : "text-gray-400 group-hover:text-gray-700"}`}
          strokeWidth={2}
        />
        {item.label}
      </Link>
    );
  }

  return (
    <aside className={`w-72 bg-white/80 border-r border-black/5 h-screen sticky top-0 p-5 flex flex-col backdrop-blur-xl ${className || ""}`}>
      <div className="mb-6">
        <Link href="/dashboard" className="inline-flex">
          <Logo />
        </Link>
        <div className="mt-5 rounded-2xl border border-black/[0.06] bg-gradient-to-br from-white to-gray-50 px-4 py-3 shadow-sm">
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Local workspace
          </div>
          <div className="mt-1 text-sm font-semibold text-foreground">magi-agent</div>
          <div className="mt-1 text-xs leading-5 text-secondary">
            Runtime, rules, skills, memory, and chat.
          </div>
        </div>
      </div>

      <nav className="space-y-1 flex-1 overflow-y-auto min-h-0">
        {botNavItems.map(renderNavItem)}
      </nav>

      <div className="border-t border-black/[0.06] pt-4">
        <LanguageSwitcher menuPlacement="top" />
      </div>
    </aside>
  );
}
