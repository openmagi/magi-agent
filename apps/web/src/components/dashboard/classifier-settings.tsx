import { useState } from "react";

interface DimensionDef {
  name: string;
  phase: "request" | "finalAnswer";
  description: string;
  instructions: string;
}

export interface ClassifierSettingsProps {
  sendJson: (path: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;
}

const PHASE_LABELS: Record<string, string> = {
  request: "Request phase",
  finalAnswer: "Final answer phase",
};

export function ClassifierSettings({ sendJson }: ClassifierSettingsProps) {
  const [dimensions, setDimensions] = useState<DimensionDef[]>([]);
  const [nlInput, setNlInput] = useState("");
  const [converting, setConverting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAdd = async (): Promise<void> => {
    if (!nlInput.trim()) return;
    setConverting(true);
    setError(null);
    try {
      const data = await sendJson("/api/hooks/from-natural-language", {
        description: nlInput,
        type: "classifier_dimension",
      });
      if (data.name) {
        setDimensions((prev) => [...prev, data as unknown as DimensionDef]);
        setNlInput("");
      } else {
        setError(typeof data.error === "string" ? data.error : "Conversion failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Server error");
    } finally {
      setConverting(false);
    }
  };

  const handleRemove = (name: string): void => {
    setDimensions((prev) => prev.filter((d) => d.name !== name));
  };

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-gray-100 bg-white px-5 py-4">
        <p className="mb-1 text-sm font-semibold text-foreground">Add classifier dimension</p>
        <p className="mb-3 text-xs leading-5 text-secondary">
          Describe what the classifier should detect in natural language. It runs as part of the
          same fast classification pass on every turn.
        </p>
        <div className="flex gap-3">
          <input
            type="text"
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !converting) void handleAdd();
            }}
            placeholder='e.g. "Does this involve drug dosage or medical treatment recommendations?"'
            className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary"
          />
          <button
            onClick={() => void handleAdd()}
            disabled={converting || !nlInput.trim()}
            className="rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {converting ? "Converting..." : "Add"}
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-red-500">{error}</p>}
      </div>

      {dimensions.length > 0 && (
        <div className="space-y-3">
          <p className="text-sm font-semibold text-foreground">
            Active Dimensions
            <span className="ml-2 text-xs font-normal text-secondary">{dimensions.length}/10</span>
          </p>
          <div className="space-y-2">
            {dimensions.map((dim) => (
              <div
                key={dim.name}
                className="flex items-center gap-3 rounded-xl border border-gray-100 bg-white px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {dim.description || dim.name}
                    </span>
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-secondary">
                      {PHASE_LABELS[dim.phase] ?? dim.phase}
                    </span>
                  </div>
                  <p className="truncate text-xs text-secondary">{dim.instructions}</p>
                </div>
                <button
                  onClick={() => handleRemove(dim.name)}
                  className="text-xs text-red-500 transition-colors hover:text-red-600"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {dimensions.length === 0 && (
        <p className="py-12 text-center text-sm text-secondary">
          No custom classifier dimensions configured. Built-in dimensions (intent, determinism,
          planning, deferral, grounding) run automatically.
        </p>
      )}
    </div>
  );
}
