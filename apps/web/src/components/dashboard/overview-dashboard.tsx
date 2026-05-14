import {
  Activity,
  Database,
  FolderOpen,
  Puzzle,
  Clock,
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
        {/* Agent card */}
        <DashboardCard title="Agent">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex items-center gap-3">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    runtimeStatus === "active"
                      ? "bg-emerald-400"
                      : "bg-gray-300"
                  }`}
                />
                <h3 className="text-xl font-semibold text-foreground">
                  {BOT_NAME}
                </h3>
                <StatusPill status={runtimeStatus}>
                  {runtimeStatusLabel(runtimeStatus)}
                </StatusPill>
              </div>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
                Self-hosted Magi runtime with local chat, workspace knowledge,
                runtime proof, editable operator files, and your configured LLM
                provider.
              </p>
            </div>
          </div>
        </DashboardCard>

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
