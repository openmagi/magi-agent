"use client";

import { useState } from "react";
import { useMessages } from "@/lib/i18n";
import { VerificationRuleModal } from "./verification-rule-modal";
import { CustomToolModal } from "./custom-tool-modal";

interface CustomizeTabProps {
  botId: string;
  initialRules: string | null;
  initialAgentConfig?: Record<string, unknown>;
  disabled?: boolean;
}

export function CustomizeTab({ botId, initialRules, initialAgentConfig, disabled = false }: CustomizeTabProps): React.ReactElement {
  const t = useMessages();
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [toolModalOpen, setToolModalOpen] = useState(false);

  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {/* Verification Rules card */}
        <button
          type="button"
          onClick={() => setRuleModalOpen(true)}
          disabled={disabled}
          className="group flex items-start gap-4 rounded-2xl border border-black/[0.06] bg-white px-5 py-5 text-left transition-all hover:border-primary/20 hover:shadow-sm disabled:opacity-50"
        >
          <div className="w-10 h-10 rounded-xl bg-blue-50 flex items-center justify-center shrink-0 group-hover:bg-blue-100 transition-colors">
            <span className="text-lg">🛡️</span>
          </div>
          <div>
            <p className="text-sm font-semibold text-foreground">{t.customize.ruleCardTitle}</p>
            <p className="text-xs text-secondary mt-1 leading-relaxed">{t.customize.ruleCardDesc}</p>
          </div>
        </button>

        {/* Custom Tools card */}
        <button
          type="button"
          onClick={() => setToolModalOpen(true)}
          disabled={disabled}
          className="group flex items-start gap-4 rounded-2xl border border-black/[0.06] bg-white px-5 py-5 text-left transition-all hover:border-primary/20 hover:shadow-sm disabled:opacity-50"
        >
          <div className="w-10 h-10 rounded-xl bg-purple-50 flex items-center justify-center shrink-0 group-hover:bg-purple-100 transition-colors">
            <span className="text-lg">🔧</span>
          </div>
          <div>
            <p className="text-sm font-semibold text-foreground">{t.customize.toolCardTitle}</p>
            <p className="text-xs text-secondary mt-1 leading-relaxed">{t.customize.toolCardDesc}</p>
          </div>
        </button>
      </div>

      <VerificationRuleModal
        botId={botId}
        initialRules={initialRules}
        initialAgentConfig={initialAgentConfig as { builtin_presets?: Record<string, { enabled: boolean; mode: "hybrid" | "deterministic" | "llm" }> } | undefined}
        open={ruleModalOpen}
        onClose={() => setRuleModalOpen(false)}
      />

      <CustomToolModal
        botId={botId}
        open={toolModalOpen}
        onClose={() => setToolModalOpen(false)}
      />
    </>
  );
}
