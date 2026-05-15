import { useState, useEffect, useCallback } from "react";
import { ButtonLike } from "../shared";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ToolEntry {
  name: string;
  description: string;
  enabled: boolean;
  source: "builtin" | "skill" | "external";
}

export interface CustomToolModalProps {
  open: boolean;
  onClose: () => void;
  getJson: (path: string) => Promise<Record<string, unknown>>;
  sendJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
  putJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
  deleteJson: (
    path: string,
    body: Record<string, unknown>,
  ) => Promise<Record<string, unknown>>;
}

/* ------------------------------------------------------------------ */
/*  Data                                                               */
/* ------------------------------------------------------------------ */

const SOURCE_LABELS: Record<string, string> = {
  builtin: "System",
  skill: "Skill",
  external: "Custom",
};

const SOURCE_BADGE: Record<string, string> = {
  builtin: "bg-white/5 text-secondary",
  skill: "bg-purple-500/10 text-purple-400",
  external: "bg-blue-500/10 text-blue-400",
};

/* ------------------------------------------------------------------ */
/*  Modal                                                              */
/* ------------------------------------------------------------------ */

export function CustomToolModal({
  open,
  onClose,
  getJson,
  putJson,
  deleteJson,
}: CustomToolModalProps) {
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formBody, setFormBody] = useState("");
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getJson("/v1/admin/tools");
      const list = Array.isArray(data.tools)
        ? (data.tools as ToolEntry[])
        : [];
      setTools(list);
    } catch {
      /* runtime may not be connected */
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [getJson]);

  useEffect(() => {
    if (open && !fetched) void fetchTools();
  }, [open, fetched, fetchTools]);

  const handleToggle = async (
    toolName: string,
    currentEnabled: boolean,
  ): Promise<void> => {
    const action = currentEnabled ? "disable" : "enable";
    try {
      await putJson(
        `/v1/admin/tools/${encodeURIComponent(toolName)}/${action}`,
        {},
      );
      setTools((prev) =>
        prev.map((item) =>
          item.name === toolName
            ? { ...item, enabled: !currentEnabled }
            : item,
        ),
      );
    } catch {
      /* ignore */
    }
  };

  const handleDelete = async (toolName: string): Promise<void> => {
    try {
      await deleteJson(
        `/v1/admin/tools/${encodeURIComponent(toolName)}`,
        {},
      );
      setTools((prev) => prev.filter((item) => item.name !== toolName));
    } catch {
      /* ignore */
    }
  };

  const handleAddTool = async (): Promise<void> => {
    if (!formName.trim() || !formDesc.trim()) return;
    setFormSaving(true);
    setFormError(null);
    try {
      await putJson("/v1/admin/tools", {
        name: formName.trim(),
        description: formDesc.trim(),
        body: formBody.trim(),
      });
      setFormName("");
      setFormDesc("");
      setFormBody("");
      setShowForm(false);
      setFetched(false);
      void fetchTools();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Failed to add tool",
      );
    } finally {
      setFormSaving(false);
    }
  };

  const externalTools = tools.filter((item) => item.source === "external");
  const otherTools = tools.filter((item) => item.source !== "external");

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="mx-4 max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-white/10 bg-[#0f0f14] p-6 shadow-2xl">
        {/* Header */}
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-lg font-semibold text-foreground">
            Custom Tools
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer p-1 text-secondary transition-colors hover:text-foreground"
          >
            <svg
              className="h-5 w-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <p className="mb-6 text-xs text-secondary">
          Register external tools, manage built-in tool availability, and
          toggle tool access for the agent.
        </p>

        {/* Add form */}
        {showForm ? (
          <div className="mb-5 space-y-4 rounded-2xl border border-primary/15 bg-white/5 p-5">
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.12em] text-secondary/75">
                Tool Name
              </span>
              <input
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="my_custom_tool"
                className="w-full rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5 text-sm text-foreground outline-none placeholder:text-secondary/45 focus:border-primary/45 focus:ring-4 focus:ring-primary/10"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.12em] text-secondary/75">
                Description
              </span>
              <input
                value={formDesc}
                onChange={(e) => setFormDesc(e.target.value)}
                placeholder="What this tool does"
                className="w-full rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5 text-sm text-foreground outline-none placeholder:text-secondary/45 focus:border-primary/45 focus:ring-4 focus:ring-primary/10"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.12em] text-secondary/75">
                Tool Body (optional)
              </span>
              <textarea
                value={formBody}
                onChange={(e) => setFormBody(e.target.value)}
                placeholder="JSON schema or implementation details..."
                rows={5}
                className="w-full resize-y rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5 font-mono text-sm text-foreground outline-none placeholder:text-secondary/45 focus:border-primary/45 focus:ring-4 focus:ring-primary/10"
              />
            </label>
            {formError && (
              <p className="text-xs text-red-500">{formError}</p>
            )}
            <div className="flex items-center gap-3 pt-1">
              <ButtonLike
                variant="ghost"
                onClick={() => {
                  setShowForm(false);
                  setFormError(null);
                }}
              >
                Cancel
              </ButtonLike>
              <ButtonLike
                onClick={() => void handleAddTool()}
                disabled={formSaving || !formName.trim() || !formDesc.trim()}
              >
                {formSaving ? "Adding..." : "Add Tool"}
              </ButtonLike>
            </div>
          </div>
        ) : (
          <ButtonLike
            variant="secondary"
            className="!mb-5 !w-full !border-dashed !border-2"
            onClick={() => setShowForm(true)}
          >
            + Add New Tool
          </ButtonLike>
        )}

        {/* Tool list */}
        {loading ? (
          <div className="flex flex-col items-center py-10">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="mt-3 text-xs text-secondary">Loading tools...</p>
          </div>
        ) : (
          <div className="space-y-5">
            {externalTools.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-semibold text-foreground">
                  Custom Tools
                </p>
                {externalTools.map((tool) => (
                  <div
                    key={tool.name}
                    className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {tool.name}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-secondary">
                        {tool.description}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleToggle(tool.name, tool.enabled)}
                      className={`relative inline-flex h-6 w-10 cursor-pointer items-center rounded-full transition-colors duration-200 ${
                        tool.enabled ? "bg-primary" : "bg-white/10"
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                          tool.enabled ? "translate-x-5" : "translate-x-0.5"
                        }`}
                      />
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(tool.name)}
                      className="cursor-pointer text-secondary transition-colors hover:text-red-500"
                    >
                      <svg
                        className="h-4 w-4"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M6 18L18 6M6 6l12 12"
                        />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}

            {otherTools.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-semibold text-foreground">
                  System Tools
                </p>
                {otherTools.map((tool) => (
                  <div
                    key={tool.name}
                    className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/5 px-4 py-3"
                  >
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${SOURCE_BADGE[tool.source]}`}
                    >
                      {SOURCE_LABELS[tool.source]}
                    </span>
                    <p className="flex-1 truncate text-sm font-medium">
                      {tool.name}
                    </p>
                    <button
                      type="button"
                      onClick={() => void handleToggle(tool.name, tool.enabled)}
                      className={`relative inline-flex h-6 w-10 cursor-pointer items-center rounded-full transition-colors duration-200 ${
                        tool.enabled ? "bg-primary" : "bg-white/10"
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                          tool.enabled ? "translate-x-5" : "translate-x-0.5"
                        }`}
                      />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {tools.length === 0 && fetched && (
              <div className="flex flex-col items-center py-10">
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-white/5">
                  <svg
                    className="h-6 w-6 text-secondary"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M11.42 15.17l-5.1-3.05A2.25 2.25 0 004.5 14.2V18a2.25 2.25 0 001.82 2.12l5.1 3.05a2.25 2.25 0 002.16 0l5.1-3.05A2.25 2.25 0 0019.5 18v-3.8a2.25 2.25 0 00-1.82-2.08l-5.1-3.05a2.25 2.25 0 00-2.16 0z"
                    />
                  </svg>
                </div>
                <p className="text-sm text-secondary">No tools registered</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
