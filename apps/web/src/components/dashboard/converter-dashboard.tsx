import { FileOutput, MessageSquare, Database, FolderOpen } from "lucide-react";
import {
  DashboardPageHeader,
  DashboardCard,
  ButtonLike,
  type AppRoute,
} from "./shared";

export interface ConverterDashboardProps {
  onNavigate: (route: AppRoute) => void;
}

export function ConverterDashboard({ onNavigate }: ConverterDashboardProps) {
  return (
    <div className="max-w-3xl space-y-6">
      <DashboardPageHeader
        eyebrow="Artifacts"
        title="Converter"
        description="Local document conversion runs through the agent workspace and artifact pipeline."
      />
      <DashboardCard title="Local Conversion Flow">
        <div className="space-y-4 text-sm leading-6 text-secondary">
          <p>
            Drop files into chat or upload them to Knowledge, then ask Magi to
            convert, summarize, extract tables, or generate deliverable
            artifacts. Outputs appear in the Work inspector and workspace
            artifacts.
          </p>
          <div className="flex flex-wrap gap-3">
            <ButtonLike onClick={() => onNavigate("chat")}>
              <MessageSquare className="mr-2 h-4 w-4" />
              Open Chat
            </ButtonLike>
            <ButtonLike
              variant="secondary"
              onClick={() => onNavigate("knowledge")}
            >
              <Database className="mr-2 h-4 w-4" />
              Upload Knowledge
            </ButtonLike>
            <ButtonLike
              variant="secondary"
              onClick={() => onNavigate("workspace")}
            >
              <FolderOpen className="mr-2 h-4 w-4" />
              Open Workspace
            </ButtonLike>
          </div>
        </div>
      </DashboardCard>
      <DashboardCard title="Supported Patterns">
        <div className="grid gap-3 sm:grid-cols-2">
          {[
            {
              label: "DOCX/PDF/PPTX/XLSX extraction",
              icon: FileOutput,
            },
            {
              label: "Markdown and structured report generation",
              icon: FileOutput,
            },
            {
              label: "Workspace artifact review",
              icon: FolderOpen,
            },
            {
              label: "Runtime proof before completion",
              icon: FileOutput,
            },
          ].map((item) => (
            <div
              key={item.label}
              className="flex items-center gap-3 rounded-xl border border-black/[0.06] bg-gray-50 px-4 py-3 transition-colors hover:bg-gray-100/80"
            >
              <item.icon className="h-4 w-4 shrink-0 text-primary/50" />
              <span className="text-sm font-semibold text-foreground">
                {item.label}
              </span>
            </div>
          ))}
        </div>
      </DashboardCard>
    </div>
  );
}
