"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
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

  const LOCAL_BOT_ID = "local";
  const botPrefix = `/dashboard/${LOCAL_BOT_ID}`;

  const botNavItems = [
    { href: `${botPrefix}/chat`, label: "Chat", prefix: `${botPrefix}/chat` },
    { href: `${botPrefix}/overview`, label: t.dashboard.overview },
    { href: `${botPrefix}/settings`, label: t.dashboard.settings },
    { href: `${botPrefix}/customize`, label: t.customize?.tabCustomize ?? "Customize" },
    { href: `${botPrefix}/skills`, label: t.dashboard.skills },
    { href: `${botPrefix}/memory`, label: t.dashboard.memory },
    { href: `${botPrefix}/knowledge`, label: t.dashboard.knowledge },
  ];

  function isActive(item: { href: string; prefix?: string }): boolean {
    const matchPath = item.prefix || item.href;
    return pathname.startsWith(matchPath);
  }

  function renderNavItem(item: { href: string; label: string; prefix?: string }) {
    const active = isActive(item);
    return (
      <Link
        key={item.href}
        href={item.href}
        onClick={() => onNavigate?.()}
        className={`block px-3 py-2 rounded-xl text-[13px] font-medium transition-colors duration-200 cursor-pointer ${
          active
            ? "bg-primary/10 text-primary-light border border-primary/25"
            : "text-gray-700 hover:text-gray-900 hover:bg-gray-100"
        }`}
      >
        {item.label}
      </Link>
    );
  }

  return (
    <aside className={`w-64 bg-gray-50 border-r border-gray-200 h-screen sticky top-0 p-6 flex flex-col ${className || ""}`}>
      <div className="mb-10">
        <Link href="/dashboard">
          <Logo />
        </Link>
      </div>

      <nav className="space-y-1 flex-1 overflow-y-auto min-h-0">
        {botNavItems.map(renderNavItem)}
      </nav>

      <div className="space-y-2">
        <LanguageSwitcher menuPlacement="top" />
      </div>
    </aside>
  );
}
