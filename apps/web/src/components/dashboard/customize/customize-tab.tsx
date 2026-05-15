import { useState } from "react";
import { VerificationRuleModal } from "./verification-rule-modal";
import { CustomToolModal } from "./custom-tool-modal";

export interface CustomizeTabProps {
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

export function CustomizeTab({
  getJson,
  sendJson,
  putJson,
  deleteJson,
}: CustomizeTabProps) {
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [toolModalOpen, setToolModalOpen] = useState(false);

  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {/* Verification Rules card */}
        <button
          type="button"
          onClick={() => setRuleModalOpen(true)}
          className="group flex cursor-pointer items-start gap-4 rounded-2xl border border-white/10 bg-white/5 px-5 py-5 text-left backdrop-blur-xl transition-all duration-200 hover:border-primary/20 hover:bg-white/[0.08]"
        >
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-500/10 transition-colors group-hover:bg-emerald-500/20">
            <svg
              className="h-5 w-5 text-emerald-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z"
              />
            </svg>
          </div>
          <div>
            <p className="text-sm font-semibold text-foreground">
              Verification Rules
            </p>
            <p className="mt-1 text-xs leading-relaxed text-secondary">
              Configure verification engines, custom rules, and hook presets
              that check every response before delivery.
            </p>
          </div>
        </button>

        {/* Custom Tools card */}
        <button
          type="button"
          onClick={() => setToolModalOpen(true)}
          className="group flex cursor-pointer items-start gap-4 rounded-2xl border border-white/10 bg-white/5 px-5 py-5 text-left backdrop-blur-xl transition-all duration-200 hover:border-primary/20 hover:bg-white/[0.08]"
        >
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-500/10 transition-colors group-hover:bg-blue-500/20">
            <svg
              className="h-5 w-5 text-blue-400"
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
          <div>
            <p className="text-sm font-semibold text-foreground">
              Custom Tools
            </p>
            <p className="mt-1 text-xs leading-relaxed text-secondary">
              Register external tools, manage built-in tool availability, and
              toggle tool access for the agent.
            </p>
          </div>
        </button>
      </div>

      <VerificationRuleModal
        open={ruleModalOpen}
        onClose={() => setRuleModalOpen(false)}
        getJson={getJson}
        sendJson={sendJson}
      />

      <CustomToolModal
        open={toolModalOpen}
        onClose={() => setToolModalOpen(false)}
        getJson={getJson}
        sendJson={sendJson}
        putJson={putJson}
        deleteJson={deleteJson}
      />
    </>
  );
}
