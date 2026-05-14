import {
  DashboardPageHeader,
  DashboardCard,
  MetricTile,
  EmptyState,
  asString,
  asRecord,
  asArray,
  runtimeItemCount,
  type JsonRecord,
} from "./shared";

export interface UsageDashboardProps {
  runtimeSnapshot: JsonRecord | null;
}

export function UsageDashboard({ runtimeSnapshot }: UsageDashboardProps) {
  const sectionRows = [
    { key: "sessions", title: "Sessions" },
    { key: "tasks", title: "Background Tasks" },
    { key: "crons", title: "Schedules" },
    { key: "artifacts", title: "Artifacts" },
    { key: "tools", title: "Tools" },
  ].map((section) => {
    const data = asRecord(runtimeSnapshot?.[section.key]);
    const items = asArray(data.items).slice(0, 6);
    return {
      ...section,
      count: runtimeItemCount(runtimeSnapshot, section.key),
      items,
    };
  });

  return (
    <div className="max-w-4xl space-y-6">
      <DashboardPageHeader
        eyebrow="Runtime Activity"
        title="Usage"
        description="Local runtime activity. Self-hosted Magi does not meter platform credits."
      />
      <DashboardCard title="Runtime Totals">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {sectionRows.map((row) => (
            <MetricTile key={row.key} label={row.title} value={row.count} />
          ))}
        </div>
        <p className="mt-5 text-sm leading-6 text-secondary">
          Model usage is controlled by the provider or local model server you
          configure.
        </p>
      </DashboardCard>
      <div className="grid gap-5 lg:grid-cols-2">
        {sectionRows.map((row) => (
          <DashboardCard key={row.key} title={row.title}>
            {row.items.length === 0 ? (
              <EmptyState>No {row.title.toLowerCase()} reported.</EmptyState>
            ) : (
              <div className="space-y-2">
                {row.items.map((item, index) => {
                  const label =
                    asString(item.name) ||
                    asString(item.id) ||
                    asString(item.sessionKey) ||
                    asString(item.taskId) ||
                    `${row.title} ${index + 1}`;
                  const detail =
                    asString(item.status) ||
                    asString(item.state) ||
                    asString(item.schedule) ||
                    asString(item.description);
                  return (
                    <div
                      key={`${row.key}-${index}`}
                      className="rounded-xl border border-black/[0.06] bg-gray-50 px-4 py-3 transition-colors hover:bg-gray-100/80"
                    >
                      <div className="truncate text-sm font-semibold text-foreground">
                        {label}
                      </div>
                      {detail && (
                        <div className="mt-1 text-xs text-secondary">
                          {detail}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </DashboardCard>
        ))}
      </div>
    </div>
  );
}
