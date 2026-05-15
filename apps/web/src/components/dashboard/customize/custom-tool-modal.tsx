"use client";

import { useState, useEffect, useCallback } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { useAuthFetch } from "@/hooks/use-auth-fetch";

interface CustomToolModalProps {
  botId: string;
  open: boolean;
  onClose: () => void;
}

interface ToolEntry {
  name: string;
  description: string;
  enabled: boolean;
  source: "builtin" | "skill" | "external";
}

export function CustomToolModal({ botId, open, onClose }: CustomToolModalProps): React.ReactElement | null {
  const authFetch = useAuthFetch();

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
      const res = await authFetch(`/v1/tools`);
      if (res.ok) {
        const data: { tools?: ToolEntry[] } = await res.json();
        setTools(data.tools ?? []);
      }
    } catch { /* ignore */ } finally { setLoading(false); setFetched(true); }
  }, [authFetch]);

  useEffect(() => { if (open && !fetched) void fetchTools(); }, [open, fetched, fetchTools]);

  const handleToggle = async (toolName: string, currentEnabled: boolean): Promise<void> => {
    const action = currentEnabled ? "disable" : "enable";
    await authFetch(`/v1/tools/${encodeURIComponent(toolName)}/${action}`, { method: "PUT" });
    setTools((prev) => prev.map((item) => (item.name === toolName ? { ...item, enabled: !currentEnabled } : item)));
  };

  const handleDelete = async (toolName: string): Promise<void> => {
    const res = await authFetch(`/v1/tools/${encodeURIComponent(toolName)}`, { method: "DELETE" });
    if (res.ok) setTools((prev) => prev.filter((item) => item.name !== toolName));
  };

  const handleAddTool = async (): Promise<void> => {
    if (!formName.trim() || !formDesc.trim()) return;
    setFormSaving(true); setFormError(null);
    try {
      const res = await authFetch(`/v1/tools`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: formName.trim(), description: formDesc.trim(), body: formBody.trim() }),
      });
      if (!res.ok) { const data = await res.json(); throw new Error(data.error ?? "Failed to add tool"); }
      setFormName(""); setFormDesc(""); setFormBody(""); setShowForm(false); setFetched(false); void fetchTools();
    } catch (err) { setFormError(err instanceof Error ? err.message : "Failed to add tool"); } finally { setFormSaving(false); }
  };

  const SOURCE_LABEL: Record<string, string> = { builtin: "Built-in", skill: "Skill", external: "External" };
  const SOURCE_BADGE: Record<string, string> = { builtin: "bg-black/5 text-secondary", skill: "bg-primary/10 text-primary", external: "bg-cta/10 text-cta" };

  const externalTools = tools.filter((item) => item.source === "external");
  const otherTools = tools.filter((item) => item.source !== "external");

  if (!open) return null;

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        {/* Header */}
        <div className="flex items-start justify-between mb-1">
          <h2 className="text-lg font-semibold text-foreground">Custom Tools</h2>
          <button type="button" onClick={onClose} className="text-secondary hover:text-foreground transition-colors p-1 -mr-1 -mt-1">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <p className="text-xs text-secondary mb-6">Manage the tools available to your agent. Toggle built-in tools on/off or add custom ones.</p>

        {/* Add form */}
        {showForm ? (
          <div className="glass rounded-2xl p-5 mb-5 !border-primary/15 space-y-4">
            <Input label="Tool Name" value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="e.g. weather_lookup" />
            <Input label="Description" value={formDesc} onChange={(e) => setFormDesc(e.target.value)} placeholder="What does this tool do?" />
            <Textarea label="Implementation" value={formBody} onChange={(e) => setFormBody(e.target.value)} placeholder="Tool implementation or endpoint URL..." rows={5} className="!font-mono !resize-y" />
            {formError && <p className="text-xs text-red-500">{formError}</p>}
            <div className="flex items-center gap-3 pt-1">
              <Button variant="ghost" size="sm" onClick={() => { setShowForm(false); setFormError(null); }}>Cancel</Button>
              <Button variant="cta" size="sm" onClick={() => void handleAddTool()} disabled={formSaving || !formName.trim() || !formDesc.trim()}>
                {formSaving ? "Saving..." : "Add Tool"}
              </Button>
            </div>
          </div>
        ) : (
          <Button variant="secondary" className="!w-full !border-dashed !border-2 mb-5" onClick={() => setShowForm(true)}>
            + Add Custom Tool
          </Button>
        )}

        {/* Tool list */}
        {loading ? (
          <div className="flex flex-col items-center py-10">
            <div className="h-5 w-5 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            <p className="text-xs text-secondary mt-3">Loading tools...</p>
          </div>
        ) : (
          <div className="space-y-5">
            {externalTools.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-semibold text-foreground">Custom Tools</p>
                {externalTools.map((tool) => (
                  <div key={tool.name} className="glass rounded-2xl px-4 py-3.5 flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{tool.name}</p>
                      <p className="text-xs text-secondary truncate mt-0.5">{tool.description}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleToggle(tool.name, tool.enabled)}
                      className={`relative inline-flex h-6 w-10 items-center rounded-full transition-colors duration-200 ${tool.enabled ? "bg-primary" : "bg-black/10"}`}
                    >
                      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${tool.enabled ? "translate-x-5" : "translate-x-0.5"}`} />
                    </button>
                    <button type="button" onClick={() => void handleDelete(tool.name)} className="text-secondary hover:text-red-500 transition-colors">
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>
                  </div>
                ))}
              </div>
            )}

            {otherTools.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-semibold text-foreground">System Tools</p>
                {otherTools.map((tool) => (
                  <div key={tool.name} className="glass rounded-2xl px-4 py-3 flex items-center gap-3">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${SOURCE_BADGE[tool.source]}`}>{SOURCE_LABEL[tool.source]}</span>
                    <p className="flex-1 text-sm font-medium truncate">{tool.name}</p>
                    <button
                      type="button"
                      onClick={() => void handleToggle(tool.name, tool.enabled)}
                      className={`relative inline-flex h-6 w-10 items-center rounded-full transition-colors duration-200 ${tool.enabled ? "bg-primary" : "bg-black/10"}`}
                    >
                      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${tool.enabled ? "translate-x-5" : "translate-x-0.5"}`} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {tools.length === 0 && fetched && (
              <div className="flex flex-col items-center py-10">
                <div className="w-12 h-12 rounded-2xl bg-black/5 flex items-center justify-center mb-3"><span className="text-xl">🔧</span></div>
                <p className="text-sm text-secondary">No tools configured yet</p>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
