import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { KbSidePanel } from "./kb-side-panel";
import type { ChannelState } from "@/lib/chat/types";
import type { KbCollectionWithDocs } from "@/hooks/use-kb-docs";
import type { WorkspaceFileEntry } from "@/lib/workspace/workspace-files";

function channelState(overrides: Partial<ChannelState> = {}): ChannelState {
  return {
    streaming: false,
    streamingText: "",
    thinkingText: "",
    error: null,
    hasTextContent: false,
    thinkingStartedAt: null,
    turnPhase: null,
    heartbeatElapsedMs: null,
    pendingInjectionCount: 0,
    activeTools: [],
    subagents: [],
    taskBoard: null,
    fileProcessing: false,
    ...overrides,
  };
}

const collections: KbCollectionWithDocs[] = [
  {
    id: "personal",
    name: "Personal KB",
    scope: "personal",
    orgId: null,
    docs: [
      {
        id: "doc-1",
        filename: "notes.md",
        status: "ready",
        scope: "personal",
        orgId: null,
        collectionId: "personal",
        collectionName: "Personal KB",
      },
    ],
  },
  {
    id: "org",
    name: "Org KB",
    scope: "org",
    orgId: "org-1",
    docs: [
      {
        id: "doc-2",
        filename: "org.md",
        status: "ready",
        scope: "org",
        orgId: "org-1",
        collectionId: "org",
        collectionName: "Org KB",
      },
    ],
  },
];

function renderWithLocalStorage<T>(values: Record<string, string>, render: () => T): T {
  const store = new Map(Object.entries(values));
  const localStorageMock = {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value);
    }),
  };
  vi.stubGlobal("window", { localStorage: localStorageMock });
  vi.stubGlobal("localStorage", localStorageMock);
  try {
    return render();
  } finally {
    vi.unstubAllGlobals();
  }
}

function renderPanel(
  workState: Partial<ChannelState> = {},
  options: { workspaceFiles?: WorkspaceFileEntry[]; missionChannelId?: string | null } = {},
): string {
  return renderToStaticMarkup(
    <KbSidePanel
      botId="bot-1"
      collections={collections}
      loading={false}
      refreshing={false}
      workspaceFiles={options.workspaceFiles ?? [
        {
          path: "outputs/report.md",
          filename: "report.md",
          size: 120,
          modifiedAt: null,
          previewKind: "markdown",
        },
      ]}
      workspaceLoading={false}
      workspaceRefreshing={false}
      selectedDocs={[]}
      onToggleDoc={vi.fn()}
      onRefresh={vi.fn()}
      onWorkspaceRefresh={vi.fn()}
      missionChannelType="app"
      missionChannelId={options.missionChannelId ?? "general"}
      channelState={channelState(workState)}
      queuedMessages={[
        {
          id: "q1",
          content: "follow up after run",
          queuedAt: 1,
        },
      ]}
      controlRequests={[]}
    />,
  );
}

describe("KbSidePanel", () => {
  it("defaults to Knowledge so active inline work is not duplicated in the right panel", () => {
    const html = renderPanel({
      streaming: true,
      turnPhase: "executing",
      activeTools: [
        {
          id: "tool-1",
          label: "Bash",
          status: "running",
          startedAt: 1,
          inputPreview: "npm test",
        },
      ],
    });

    expect(html).toContain("Work");
    expect(html).toContain("Missions");
    expect(html).toContain("Knowledge");
    expect(html).toContain("Knowledge Base");
    expect(html).not.toContain("Work in progress");
    expect(html).not.toContain("Checking the work");
    expect(html).not.toContain("Running tests");
    expect(html).not.toContain("npm test");
    expect(html).not.toContain("Bash");
    expect(html).not.toContain("follow up after run");
  });

  it("renders durable mission state through the Missions inspector tab", () => {
    const html = renderPanel(
      {
        missions: [
          {
            id: "mission-1",
            title: "Draft weekly research report",
            kind: "goal",
            status: "blocked",
            detail: "Waiting for approval",
            updatedAt: 123,
          },
        ],
        activeGoalMissionId: "mission-1",
      },
      { missionChannelId: "stock" },
    );

    expect(html).toContain('aria-label="Missions ledger"');
    expect(html).toContain('data-mission-channel-type="app"');
    expect(html).toContain('data-mission-channel-id="stock"');
    expect(html).toContain("Active goal");
    expect(html).toContain("Draft weekly research report");
    expect(html).toContain("Waiting for approval");
    expect(html).toContain("Unblock");
  });

  it("shows a pending goal mission while the runtime is starting the ledger entry", () => {
    const html = renderPanel({
      streaming: true,
      pendingGoalMissionTitle: "Review all uploaded evidence",
    });

    expect(html).toContain("Active goal");
    expect(html).toContain("Review all uploaded evidence");
    expect(html).toContain("Starting mission");
  });

  it("preserves the existing knowledge scope controls in the panel markup", () => {
    const html = renderPanel();

    expect(html).toContain("Knowledge Base");
    expect(html).toContain("Personal");
    expect(html).toContain("Org");
    expect(html).toContain("Workspace");
  });

  it("renders generated workspace files as a folder hierarchy", () => {
    const html = renderWithLocalStorage(
      {
        "clawy:kbPanelScope": "workspace",
        "clawy:rightInspectorView:v2": "knowledge",
      },
      () =>
        renderPanel({}, {
          workspaceFiles: [
            {
              path: "outputs/reports/one-plus-one-benchmark.pdf",
              filename: "one-plus-one-benchmark.pdf",
              size: 2048,
              modifiedAt: null,
              previewKind: "pdf",
            },
            {
              path: "outputs/reports/one-plus-one-consensus.md",
              filename: "one-plus-one-consensus.md",
              size: 1024,
              modifiedAt: null,
              previewKind: "markdown",
            },
            {
              path: "outputs/audit-test.md",
              filename: "audit-test.md",
              size: 20,
              modifiedAt: null,
              previewKind: "markdown",
            },
          ],
        }),
    );

    expect(html).toContain('role="tree"');
    expect(html).toContain('aria-label="Generated files"');
    expect(html).toContain("Generated files");
    expect(html).toContain("outputs");
    expect(html).toContain("reports");
    expect(html).toContain("one-plus-one-benchmark.pdf");
    expect(html).toContain("outputs/reports/one-plus-one-benchmark.pdf");
    expect(html.indexOf("outputs")).toBeLessThan(html.indexOf("reports"));
    expect(html.indexOf("reports")).toBeLessThan(html.indexOf("one-plus-one-benchmark.pdf"));
  });
});
