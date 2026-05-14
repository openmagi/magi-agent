import {
  MessageSquare,
  LayoutDashboard,
  Settings,
  BarChart3,
  Puzzle,
  FileStack,
  FileText,
  Brain,
  FolderOpen,
  RefreshCw,
  ArrowLeft,
} from "lucide-react";
import type { ReactNode } from "react";
import {
  runtimeStatusLabel,
  type AppRoute,
  type DashboardRoute,
  type RuntimeCheckStatus,
} from "./shared";

const BOT_NAME = "Magi_Local";

const ICON_MAP: Record<string, ReactNode> = {
  chat: <MessageSquare className="h-4 w-4" />,
  overview: <LayoutDashboard className="h-4 w-4" />,
  settings: <Settings className="h-4 w-4" />,
  usage: <BarChart3 className="h-4 w-4" />,
  skills: <Puzzle className="h-4 w-4" />,
  converter: <FileStack className="h-4 w-4" />,
  knowledge: <FileText className="h-4 w-4" />,
  memory: <Brain className="h-4 w-4" />,
  workspace: <FolderOpen className="h-4 w-4" />,
};

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
  const primaryItems: Array<{ route: AppRoute; label: string }> = [
    { route: "chat", label: "Chat" },
    { route: "overview", label: "Overview" },
    { route: "settings", label: "Settings" },
    { route: "usage", label: "Usage" },
    { route: "skills", label: "Skills" },
    { route: "converter", label: "Converter" },
  ];
  const workspaceItems: Array<{ route: AppRoute; label: string }> = [
    { route: "knowledge", label: "Knowledge" },
    { route: "memory", label: "Memory" },
    { route: "workspace", label: "Workspace" },
  ];

  const renderItem = ({
    route,
    label,
  }: {
    route: AppRoute;
    label: string;
  }) => {
    const active = route === activeRoute;
    return (
      <button
        key={route}
        type="button"
        onClick={() => onNavigate(route)}
        className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-sm font-medium transition-colors duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${
          active
            ? "border border-primary/20 bg-primary/10 text-primary-light"
            : "border border-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-950"
        }`}
      >
        <span
          className={active ? "text-primary" : "text-gray-400"}
        >
          {ICON_MAP[route]}
        </span>
        {label}
      </button>
    );
  };

  return (
    <aside className="hidden h-screen w-64 shrink-0 flex-col border-r border-gray-200 bg-gray-50 p-6 md:flex">
      {/* Brand */}
      <button
        type="button"
        onClick={() => onNavigate("overview")}
        className="mb-10 flex min-h-11 cursor-pointer items-center gap-3 rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
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

      {/* Bot status card */}
      <div className="mb-4 rounded-xl border border-gray-200 bg-white px-3.5 py-3">
        <div className="min-w-0 truncate text-sm font-semibold text-foreground">
          {BOT_NAME}
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs font-medium text-secondary">
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              runtimeStatus === "active" ? "bg-emerald-400" : "bg-gray-300"
            }`}
          />
          {runtimeStatusLabel(runtimeStatus)}
        </div>
      </div>

      {/* Navigation */}
      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto">
        <div className="pb-1">
          <span className="px-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
            Chat
          </span>
        </div>
        <div className="space-y-0.5">{primaryItems.map(renderItem)}</div>
        <div className="pt-5 pb-1">
          <div className="mb-3 border-t border-gray-200" />
          <span className="px-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
            Local Runtime
          </span>
        </div>
        <div className="space-y-0.5">{workspaceItems.map(renderItem)}</div>
      </nav>

      {/* Footer actions */}
      <div className="space-y-1 border-t border-gray-200 pt-4">
        <button
          type="button"
          onClick={onRefresh}
          className="flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-xl px-3 text-left text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
        >
          <RefreshCw className="h-4 w-4 text-gray-400" />
          Refresh
        </button>
        <button
          type="button"
          onClick={() => onNavigate("chat")}
          className="flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-xl px-3 text-left text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
        >
          <ArrowLeft className="h-4 w-4 text-gray-400" />
          Back to chat
        </button>
      </div>
    </aside>
  );
}
