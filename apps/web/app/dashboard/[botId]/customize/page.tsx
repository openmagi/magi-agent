"use client";

import { CustomizeTab } from "@/components/dashboard/customize/customize-tab";

export default function CustomizePage() {
  return (
    <div className="space-y-6">
      <CustomizeTab
        botId="local"
        initialRules={null}
        initialAgentConfig={undefined}
        disabled={false}
      />
    </div>
  );
}
