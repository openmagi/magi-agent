"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { usePathname, useSearchParams, useParams, useRouter } from "next/navigation";
import { Logo } from "@/components/ui/logo";
import { LogoutButton } from "@/components/dashboard/logout-button";
import { LanguageSwitcher } from "@/components/ui/language-switcher";
import { useMessages } from "@/lib/i18n";
import { usePrivy } from "@privy-io/react-auth";

interface BotInfo {
  id: string;
  name: string;
  status: string;
}

interface OrgInfo {
  slug: string;
  name: string;
}

interface SidebarNavProps {
  showAdmin?: boolean;
  onNavigate?: () => void;
  className?: string;
  bots?: BotInfo[];
  maxBots?: number;
  org?: OrgInfo | null;
}

export function SidebarNav({ showAdmin = false, onNavigate, className, bots: serverBots = [], maxBots: serverMaxBots = 1, org: serverOrg = null }: SidebarNavProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const t = useMessages();
  const router = useRouter();
  const { getAccessToken } = usePrivy();
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const rawViewAs = searchParams.get("viewAs");
  const viewAs = rawViewAs ? decodeURIComponent(rawViewAs) : null;

  // When admin views another user, fetch that user's bots/org
  const [viewAsBots, setViewAsBots] = useState<BotInfo[] | null>(null);
  const [viewAsMaxBots, setViewAsMaxBots] = useState<number | null>(null);
  const [viewAsOrg, setViewAsOrg] = useState<OrgInfo | null | undefined>(undefined);

  useEffect(() => {
    if (!viewAs || !showAdmin) {
      setViewAsBots(null);
      setViewAsMaxBots(null);
      setViewAsOrg(undefined);
      return;
    }
    let cancelled = false;
    async function fetchViewAsData(): Promise<void> {
      try {
        const token = await getAccessToken();
        const res = await fetch(`/api/admin/users/${encodeURIComponent(viewAs!)}/dashboard`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (cancelled) return;
        const bots = (data.bots ?? [])
          .map((b: BotInfo) => ({ id: b.id, name: b.name, status: b.status }));
        setViewAsBots(bots);
        // Derive max bots from subscription plan
        const plan = data.subscription?.plan ?? "pro";
        const planMaxBots: Record<string, number> = { byok: 1, pro: 1, pro_plus: 1, max: 5, flex: 10 };
        setViewAsMaxBots(planMaxBots[plan] ?? 1);
        // org info not returned from this endpoint, hide org section for viewed user
        setViewAsOrg(null);
      } catch {
        // fall back to server data
      }
    }
    fetchViewAsData();
    return () => { cancelled = true; };
  }, [viewAs, showAdmin, getAccessToken]);

  const bots = (viewAs && viewAsBots) ? viewAsBots : serverBots;
  const maxBots = (viewAs && viewAsMaxBots !== null) ? viewAsMaxBots : serverMaxBots;
  const org = (viewAs && viewAsOrg !== undefined) ? viewAsOrg : serverOrg;

  // Extract current botId from URL params
  const currentBotId = params.botId as string | undefined;
  const activeBots = bots.filter((bot) => bot.status !== "deleted");
  const defaultBot = activeBots[0] ?? bots[0];
  const currentBot = bots.find((b) => b.id === currentBotId);
  const currentBotDeleted = currentBot?.status === "deleted";
  const showSwitcher = maxBots > 1 || bots.length > 1;
  const canAddBot = activeBots.length < maxBots;

  // Resolve current sub-path (overview, settings, usage, etc.) to preserve on bot switch
  const subPath = (() => {
    if (!currentBotId) return "overview";
    const after = pathname.split(`/dashboard/${currentBotId}/`)[1];
    return after || "overview";
  })();

  // Bot-scoped nav items — fallback to first bot when on account-level pages
  const effectiveBotId = currentBotId || defaultBot?.id;
  const botPrefix = effectiveBotId ? `/dashboard/${effectiveBotId}` : "/dashboard";
  const botNavItems = [
    { href: `${botPrefix}/chat`, label: "Chat", prefix: `${botPrefix}/chat` },
    { href: `${botPrefix}/overview`, label: t.dashboard.overview },
    { href: `${botPrefix}/settings`, label: t.dashboard.settings },
    { href: `${botPrefix}/customize`, label: t.customize?.tabCustomize ?? "Customize" },
    { href: `${botPrefix}/usage`, label: t.dashboard.usage },
    { href: `${botPrefix}/skills`, label: t.dashboard.skills },
    { href: `${botPrefix}/memory`, label: t.dashboard.memory },
  ];
  const visibleBotNavItems = currentBotDeleted
    ? [{ href: `${botPrefix}/usage`, label: t.dashboard.usage }]
    : botNavItems;

  // Account-level nav items — show in viewAs mode so admin can see user's full dashboard
  const accountNavItems = [
    { href: `${botPrefix}/cli`, label: t.dashboard.cli },
    { href: `${botPrefix}/converter`, label: t.dashboard.converter },
    { href: "/dashboard/knowledge", label: t.dashboard.knowledge },
    { href: "/dashboard/billing", label: t.dashboard.billing },
    { href: "/dashboard/support", label: t.dashboard.support },
    { href: "/dashboard/referral", label: t.dashboard.referral },
  ];

  const adminNavItems = showAdmin ? [
    { href: "/dashboard/admin/bots", label: "Workspace", prefix: "/dashboard/admin/bots" },
    { href: "/dashboard/admin", label: "Admin", excludePrefix: "/dashboard/admin/bots" },
  ] : [];

  function handleAddBot() {
    sessionStorage.setItem("clawy:open-add-bot", "1");
    const targetBotId = currentBotDeleted ? activeBots[0]?.id : currentBotId;
    const target = targetBotId ? `/dashboard/${targetBotId}/overview` : "/dashboard/new";
    // If already on overview, dispatch event directly
    if (pathname.includes("/overview")) {
      window.dispatchEvent(new CustomEvent("clawy:open-add-bot"));
    } else {
      router.push(target);
    }
  }

  function buildHref(base: string): string {
    if (viewAs && !base.startsWith("/dashboard/admin")) {
      return `${base}?viewAs=${encodeURIComponent(viewAs)}`;
    }
    return base;
  }

  function isActive(item: { href: string; prefix?: string; excludePrefix?: string }): boolean {
    const matchPath = item.prefix || item.href;
    const excl = item.excludePrefix;
    return pathname.startsWith(matchPath) && (!excl || !pathname.startsWith(excl));
  }

  function renderNavItem(item: { href: string; label: string; prefix?: string; excludePrefix?: string }) {
    const active = isActive(item);
    return (
      <Link
        key={item.href}
        href={buildHref(item.href)}
        onClick={() => { onNavigate?.(); setDropdownOpen(false); }}
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

      {/* Bot Switcher — show for multi-bot plans or when multiple bots exist */}
      {showSwitcher && (
        <div className="mb-4 relative">
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-sm font-semibold bg-white border border-gray-200 hover:border-gray-300 transition-colors"
          >
            <span className="min-w-0 flex items-center gap-2">
              <span className="truncate">{currentBot?.name ?? defaultBot?.name ?? "Bot"}</span>
              {currentBotDeleted && (
                <span className="shrink-0 rounded-full border border-gray-200 bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                  {t.dashboard.deletedBot}
                </span>
              )}
            </span>
            <svg className={`w-4 h-4 text-gray-400 transition-transform ${dropdownOpen ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {dropdownOpen && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 overflow-hidden">
              {bots.map((bot) => {
                const botHref = bot.status === "deleted" ? `/dashboard/${bot.id}/usage` : `/dashboard/${bot.id}/${subPath}`;
                return (
                  <Link
                    key={bot.id}
                    href={buildHref(botHref)}
                    onClick={() => { setDropdownOpen(false); onNavigate?.(); }}
                    className={`block px-3 py-2.5 text-sm hover:bg-gray-50 transition-colors ${
                      bot.id === currentBotId ? "bg-primary/5 text-primary-light font-medium" : "text-gray-700"
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <span className="truncate">{bot.name}</span>
                      {bot.status === "deleted" && (
                        <span className="shrink-0 rounded-full border border-gray-200 bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                          {t.dashboard.deletedBot}
                        </span>
                      )}
                    </span>
                  </Link>
                );
              })}
              {canAddBot && (
                <>
                  <div className="border-t border-gray-100" />
                  <button
                    onClick={() => { setDropdownOpen(false); onNavigate?.(); handleAddBot(); }}
                    className="block w-full text-left px-3 py-2.5 text-sm text-primary-light hover:bg-gray-50 transition-colors font-medium"
                  >
                    {t.dashboard.addNewBot}
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Bot-scoped navigation */}
      <nav className="space-y-1 flex-1 overflow-y-auto min-h-0">
        {visibleBotNavItems.map(renderNavItem)}

        {/* Account section separator */}
        {accountNavItems.length > 0 && (
          <>
            <div className="pt-4 pb-1">
              <div className="border-t border-gray-200 mb-3" />
              <span className="px-3 text-[11px] font-medium text-gray-400 uppercase tracking-wider">{t.dashboard.accountSection}</span>
            </div>
            {accountNavItems.map(renderNavItem)}
          </>
        )}

        {/* Organization section */}
        {org ? (
          <>
            <div className="pt-4 pb-1">
              <div className="border-t border-gray-200 mb-3" />
              <span className="px-3 text-[11px] font-medium text-gray-400 uppercase tracking-wider">{t.org?.title ?? "Organization"}</span>
            </div>
            {[
              { href: `/dashboard/org/${org.slug}`, label: t.org?.title ?? "Organization", prefix: `/dashboard/org/${org.slug}` },
              { href: `/dashboard/org/${org.slug}/members`, label: t.org?.members ?? "Members" },
              { href: `/dashboard/org/${org.slug}/knowledge`, label: t.org?.knowledge ?? "Org KB" },
              { href: `/dashboard/org/${org.slug}/credits`, label: t.org?.credits ?? "Credits" },
              { href: `/dashboard/org/${org.slug}/activity`, label: t.org?.activity ?? "Activity" },
            ].map(renderNavItem)}
          </>
        ) : (
          <div className="pt-4 pb-1">
            <div className="border-t border-gray-200 mb-3" />
            {renderNavItem({ href: "/dashboard/org/new", label: t.org?.createTitle ?? "Create Organization" })}
          </div>
        )}

        {/* Admin section */}
        {adminNavItems.length > 0 && (
          <>
            <div className="pt-2 pb-1">
              <div className="border-t border-gray-200 mb-3" />
            </div>
            {adminNavItems.map(renderNavItem)}
          </>
        )}
      </nav>

      <div className="space-y-2">
        <LanguageSwitcher menuPlacement="top" />
        <LogoutButton />
      </div>
    </aside>
  );
}
