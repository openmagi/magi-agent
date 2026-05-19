"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { GlassCard } from "@/components/ui/glass-card";
import { Button } from "@/components/ui/button";
import { useAgentFetch } from "@/lib/local-api";

const FILES = [
  {
    path: "USER-RULES.md",
    title: "User Rules",
    description: "Plain-language rules loaded into the agent policy layer.",
  },
  {
    path: "rules.md",
    title: "Identity Injector Rules",
    description: "Optional runtime identity-injector rules for local agents.",
  },
] as const;

type EditablePath = (typeof FILES)[number]["path"];

interface FilePayload {
  content?: string;
  error?: string;
}

async function readPayload(response: Response): Promise<FilePayload> {
  const data = (await response.json().catch(() => null)) as FilePayload | null;
  if (!response.ok && response.status !== 404) {
    throw new Error(data?.error ?? `Request failed: ${response.status}`);
  }
  return data ?? {};
}

export default function CustomizePage() {
  const agentFetch = useAgentFetch();
  const [selectedPath, setSelectedPath] = useState<EditablePath>("USER-RULES.md");
  const [content, setContent] = useState("");
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const selectedFile = FILES.find((file) => file.path === selectedPath) ?? FILES[0];

  const loadFile = useCallback(async (path: EditablePath) => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const params = new URLSearchParams({ path });
      const data = await readPayload(await agentFetch(`/v1/app/workspace/file?${params.toString()}`));
      const nextContent = data.content ?? "";
      setSelectedPath(path);
      setContent(nextContent);
      setDraft(nextContent);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load customization file");
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void loadFile(selectedPath);
  }, [loadFile, selectedPath]);

  const saveFile = useCallback(async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const response = await agentFetch("/v1/app/workspace/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selectedPath, content: draft }),
      });
      const data = await readPayload(response);
      if (!response.ok) {
        throw new Error(data.error ?? "Failed to save customization file");
      }
      setContent(draft);
      setSuccess(`Saved ${selectedPath}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save customization file");
    } finally {
      setSaving(false);
    }
  }, [agentFetch, draft, selectedPath]);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Local Customization</h1>
          <p className="text-secondary mt-1">
            Edit local workspace instruction files. Cloud bot presets and hosted provisioning are not used in OSS mode.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/dashboard/local/settings">
            <Button variant="secondary" size="sm">Runtime Settings</Button>
          </Link>
          <Link href="/dashboard/local/skills">
            <Button variant="secondary" size="sm">Skills</Button>
          </Link>
        </div>
      </div>

      {error ? (
        <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
      ) : null}
      {success ? (
        <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{success}</p>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)] gap-6">
        <GlassCard className="h-fit">
          <h2 className="text-sm font-semibold text-foreground mb-3">Instruction Files</h2>
          <div className="space-y-2">
            {FILES.map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => loadFile(file.path)}
                className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
                  selectedPath === file.path ? "bg-primary/10 text-primary-light" : "hover:bg-gray-100"
                }`}
              >
                <p className="text-sm font-medium">{file.title}</p>
                <p className="mt-1 text-xs text-secondary">{file.path}</p>
              </button>
            ))}
          </div>
        </GlassCard>

        <GlassCard className="flex min-h-[680px] flex-col overflow-hidden !p-0">
          <div className="border-b border-gray-200 px-5 py-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-foreground">{selectedFile.title}</h2>
                <p className="text-sm text-secondary mt-1">{selectedFile.description}</p>
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={saveFile}
                disabled={saving || loading || draft === content}
              >
                {saving ? "Saving..." : "Save"}
              </Button>
            </div>
          </div>
          {loading ? (
            <div className="flex flex-1 items-center justify-center text-sm text-secondary">
              Loading...
            </div>
          ) : (
            <textarea
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                setSuccess(null);
              }}
              className="min-h-[560px] flex-1 resize-none border-0 bg-transparent p-5 font-mono text-sm leading-relaxed text-foreground outline-none"
              placeholder="- Always answer in Korean when the user writes Korean.&#10;- Verify files before saying work is complete."
              spellCheck={false}
            />
          )}
          {!loading && draft !== content ? (
            <div className="border-t border-gray-200 px-5 py-2 text-xs text-amber-600">
              Unsaved changes
            </div>
          ) : null}
        </GlassCard>
      </div>
    </div>
  );
}
