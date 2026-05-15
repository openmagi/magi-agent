import {
  RefreshCw,
  ArrowLeft,
} from "lucide-react";
import {
  runtimeStatusLabel,
  type AppRoute,
  type DashboardRoute,
  type RuntimeCheckStatus,
} from "./shared";

const BOT_NAME = "Magi_Local";

export interface DashboardSidebarProps {
  activeRoute: DashboardRoute;
  runtimeStatus: RuntimeCheckStatus;
  onNavigate: (route: AppRoute) => void;
  onRefresh: () => void;
}

export function DashboardSidebar({
  activeRoute,
  runtimeStatus,
  onNavigate,
  onRefresh,
}: DashboardSidebarProps) {
  const botNavItems: Array<{ route: AppRoute; label: string }> = [
    { route: "chat", label: "Chat" },
    { route: "overview", label: "Overview" },
    { route: "settings", label: "Settings" },
    { route: "usage", label: "Usage" },
    { route: "skills", label: "Skills" },
    { route: "memory", label: "Memory" },
  ];

  const accountNavItems: Array<{ route: AppRoute; label: string }> = [
    { route: "converter", label: "Converter" },
    { route: "knowledge", label: "Knowledge" },
    { route: "workspace", label: "Workspace" },
  ];

  function renderNavItem({ route, label }: { route: AppRoute; label: string }) {
    const active = route === activeRoute;
    return (
      <button
        key={route}
        type="button"
        onClick={() => onNavigate(route)}
        className={`block w-full px-3 py-2 rounded-xl text-[13px] font-medium transition-colors duration-200 cursor-pointer text-left ${
          active
            ? "bg-primary/10 text-primary-light border border-primary/25"
            : "text-gray-700 hover:text-gray-900 hover:bg-gray-100 border border-transparent"
        }`}
      >
        {label}
      </button>
    );
  }

  return (
    <aside className="hidden h-screen w-64 shrink-0 flex-col border-r border-gray-200 bg-gray-50 p-6 md:flex">
      {/* Brand */}
      <div className="mb-10">
        <button
          type="button"
          onClick={() => onNavigate("overview")}
          className="flex cursor-pointer items-center gap-3 rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          aria-label="Open Magi dashboard"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-white shadow-[0_8px_18px_rgba(124,58,237,0.18)]">
            M
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-foreground">
              Open Magi
            </span>
            <span className="block text-xs text-secondary">Local operator</span>
          </span>
        </button>
      </div>

      {/* Bot status mini-card */}
      <div className="mb-4 rounded-xl border border-gray-200 bg-white px-3.5 py-3">
        <div className="min-w-0 truncate text-sm font-semibold text-foreground">
          {BOT_NAME}
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs font-medium text-secondary">
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              runtimeStatus === "active"
                ? "bg-emerald-400 pulse-glow"
                : "bg-gray-300"
            }`}
          />
          {runtimeStatusLabel(runtimeStatus)}
        </div>
      </div>

      {/* Navigation */}
      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto">
        {botNavItems.map(renderNavItem)}

        {/* Account section separator */}
        <div className="pt-4 pb-1">
          <div className="border-t border-gray-200 mb-3" />
          <span className="px-3 text-[11px] font-medium text-gray-400 uppercase tracking-wider">
            Local Runtime
          </span>
        </div>
        {accountNavItems.map(renderNavItem)}
      </nav>

      {/* Footer actions */}
      <div className="space-y-1 border-t border-gray-200 pt-4">
        <button
          type="button"
          onClick={onRefresh}
          className="flex w-full cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left text-[13px] font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
        >
          <RefreshCw className="h-3.5 w-3.5 text-gray-400" />
          Refresh
        </button>
        <button
          type="button"
          onClick={() => onNavigate("chat")}
          className="flex w-full cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left text-[13px] font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
        >
          <ArrowLeft className="h-3.5 w-3.5 text-gray-400" />
          Back to chat
        </button>
      </div>
    </aside>
  );
}
