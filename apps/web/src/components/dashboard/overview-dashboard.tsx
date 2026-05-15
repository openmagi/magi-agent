import {
  Activity,
  Database,
  FolderOpen,
  Puzzle,
  ListChecks,
  CalendarClock,
  FileBox,
  ArrowRight,
} from "lucide-react";
import {
  DashboardPageHeader,
  DashboardCard,
  MetricTile,
  StatusPill,
  ButtonLike,
  runtimeItemCount,
  runtimeStatusLabel,
  type AppRoute,
  type JsonRecord,
  type RuntimeCheckStatus,
} from "./shared";
import type { KbCollectionWithDocs } from "@/hooks/use-kb-docs";
import type { WorkspaceFileEntry } from "@/lib/workspace/workspace-files";

const BOT_NAME = "Magi_Local";

export interface OverviewDashboardProps {
  runtimeSnapshot: JsonRecord | null;
  runtimeStatus: RuntimeCheckStatus;
  kbCollections: KbCollectionWithDocs[];
  workspaceFiles: WorkspaceFileEntry[];
  onNavigate: (route: AppRoute) => void;
}

export function OverviewDashboard({
  runtimeSnapshot,
  runtimeStatus,
  kbCollections,
  workspaceFiles,
  onNavigate,
}: OverviewDashboardProps) {
  const docCount = kbCollections.reduce(
    (sum, collection) => sum + collection.docs.length,
    0,
  );
  const isActive = runtimeStatus === "active";

  return (
    <div className="max-w-5xl space-y-6">
      <DashboardPageHeader
        eyebrow="Local Runtime"
        title="Dashboard"
        description="Manage your local Magi agent, runtime state, workspace knowledge, and operator files from one console."
        action={
          <ButtonLike onClick={() => onNavigate("chat")}>Open Chat</ButtonLike>
        }
      />

      <div className="space-y-5">
        {/* Agent status card — openmagi.ai BotStatusCard style */}
        <section className="glass rounded-2xl p-6 shadow-none">
          {/* Header with status */}
          <div className="flex items-start justify-between mb-6">
            <div className="flex items-center gap-3">
              {isActive && (
                <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 pulse-glow" />
              )}
              {runtimeStatus === "checking" && (
                <div className="w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse" />
              )}
              {runtimeStatus === "unavailable" && (
                <div className="w-2.5 h-2.5 rounded-full bg-red-400" />
              )}
              {runtimeStatus === "not_checked" && (
                <div className="w-2.5 h-2.5 rounded-full bg-gray-300" />
              )}
              <h2 className="text-lg font-semibold text-foreground">
                {BOT_NAME}
              </h2>
            </div>
            <StatusPill status={runtimeStatus}>
              {runtimeStatusLabel(runtimeStatus)}
            </StatusPill>
          </div>

          {/* Bot info grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-4 text-sm">
            <div>
              <p className="text-secondary text-xs uppercase tracking-wider mb-1">
                Runtime
              </p>
              <p className="font-medium text-foreground">
                Self-hosted Magi Agent
              </p>
            </div>
            <div>
              <p className="text-secondary text-xs uppercase tracking-wider mb-1">
                Status
              </p>
              <p className="font-medium text-foreground">
                {runtimeStatusLabel(runtimeStatus)}
              </p>
            </div>
            <div>
              <p className="text-secondary text-xs uppercase tracking-wider mb-1">
                API Key Mode
              </p>
              <p className="font-medium text-foreground">Local env vars</p>
            </div>
            <div>
              <p className="text-secondary text-xs uppercase tracking-wider mb-1">
                KB Documents
              </p>
              <p className="font-medium text-foreground">{docCount}</p>
            </div>
          </div>

          {/* Quick links */}
          <div className="mt-6 pt-5 border-t border-gray-200">
            <div className="flex flex-col sm:flex-row gap-3">
              <ButtonLike
                variant="ghost"
                onClick={() => onNavigate("settings")}
              >
                Settings
              </ButtonLike>
              <ButtonLike variant="ghost" onClick={() => onNavigate("usage")}>
                Usage
              </ButtonLike>
              <ButtonLike
                variant="ghost"
                onClick={() => onNavigate("workspace")}
              >
                Workspace
              </ButtonLike>
            </div>
          </div>
        </section>

        {/* Runtime metrics */}
        <DashboardCard title="Runtime">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricTile
              label="Sessions"
              value={runtimeItemCount(runtimeSnapshot, "sessions")}
              icon={Activity}
            />
            <MetricTile
              label="Tasks"
              value={runtimeItemCount(runtimeSnapshot, "tasks")}
              icon={ListChecks}
            />
            <MetricTile
              label="Schedules"
              value={runtimeItemCount(runtimeSnapshot, "crons")}
              icon={CalendarClock}
            />
            <MetricTile
              label="Artifacts"
              value={runtimeItemCount(runtimeSnapshot, "artifacts")}
              icon={FileBox}
            />
          </div>
        </DashboardCard>

        {/* Local assets */}
        <DashboardCard title="Local Assets">
          <div className="grid gap-3 sm:grid-cols-3">
            <MetricTile label="KB Docs" value={docCount} icon={Database} />
            <MetricTile
              label="Workspace Files"
              value={workspaceFiles.length}
              icon={FolderOpen}
            />
            <MetricTile
              label="Skills"
              value={runtimeItemCount(runtimeSnapshot, "skills")}
              icon={Puzzle}
            />
          </div>
        </DashboardCard>

        {/* Integrations */}
        <DashboardCard title="Integrations">
          <div className="space-y-3">
            {[
              {
                title: "Local LLM Provider",
                detail:
                  "Anthropic, OpenAI, Google, or any OpenAI-compatible server.",
                route: "settings" as AppRoute,
              },
              {
                title: "Workspace Knowledge",
                detail: "Local KB documents under the runtime workspace.",
                route: "knowledge" as AppRoute,
              },
              {
                title: "Operator Files",
                detail:
                  "System prompts, contracts, harness rules, hooks, and memory.",
                route: "workspace" as AppRoute,
              },
            ].map((item) => (
              <button
                key={item.title}
                type="button"
                onClick={() => onNavigate(item.route)}
                className="group flex min-h-16 w-full cursor-pointer items-center justify-between rounded-xl border border-black/[0.06] bg-gray-50 px-4 py-3 text-left transition-all duration-200 hover:border-primary/20 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
              >
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-foreground">
                    {item.title}
                  </div>
                  <div className="mt-1 text-xs text-secondary">
                    {item.detail}
                  </div>
                </div>
                <ArrowRight className="h-4 w-4 shrink-0 text-gray-300 transition-colors group-hover:text-primary" />
              </button>
            ))}
          </div>
        </DashboardCard>
      </div>
    </div>
  );
}
