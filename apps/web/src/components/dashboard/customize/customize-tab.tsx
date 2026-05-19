"use client";

import { useState } from "react";
import { useMessages } from "@/lib/i18n";
import { VerificationRuleModal } from "./verification-rule-modal";
import { CustomToolModal } from "./custom-tool-modal";
import type { AgentConfig } from "@/lib/agent-config";

interface CustomizeTabProps {
  botId: string;
  initialRules: string | null;
  initialAgentConfig?: AgentConfig;
  disabled?: boolean;
}

export function CustomizeTab({ botId, initialRules, initialAgentConfig, disabled = false }: CustomizeTabProps): React.ReactElement {
  const t = useMessages();
  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [toolModalOpen, setToolModalOpen] = useState(false);

  return (
    <>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {/* Verification Rules card */}
        <button
          type="button"
          onClick={() => setRuleModalOpen(true)}
          disabled={disabled}
          className="group flex items-start gap-3 rounded-lg border border-black/[0.06] bg-white px-4 py-3 text-left transition-all hover:border-primary/20 hover:shadow-sm disabled:opacity-50"
        >
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 transition-colors group-hover:bg-blue-100">
            <span className="text-sm">🛡️</span>
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">{t.customize.ruleCardTitle}</p>
            <p className="mt-0.5 text-xs leading-snug text-secondary">{t.customize.ruleCardDesc}</p>
          </div>
        </button>

        {/* Custom Tools card */}
        <button
          type="button"
          onClick={() => setToolModalOpen(true)}
          disabled={disabled}
          className="group flex items-start gap-3 rounded-lg border border-black/[0.06] bg-white px-4 py-3 text-left transition-all hover:border-primary/20 hover:shadow-sm disabled:opacity-50"
        >
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-purple-50 transition-colors group-hover:bg-purple-100">
            <span className="text-sm">🔧</span>
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">{t.customize.toolCardTitle}</p>
            <p className="mt-0.5 text-xs leading-snug text-secondary">{t.customize.toolCardDesc}</p>
          </div>
        </button>
      </div>

      <VerificationRuleModal
        botId={botId}
        initialRules={initialRules}
        initialAgentConfig={initialAgentConfig}
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
