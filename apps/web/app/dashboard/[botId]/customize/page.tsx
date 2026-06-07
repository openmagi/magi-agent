"use client";

import { useParams } from "next/navigation";
import { CustomizeTab } from "@/components/dashboard/customize/customize-tab";

export default function CustomizePage() {
  const params = useParams<{ botId?: string | string[] }>();
  const rawBotId = params?.botId;
  const botId = Array.isArray(rawBotId) ? rawBotId[0] : rawBotId ?? "local";

  return (
    <div className="max-w-5xl space-y-4">
      <CustomizeTab
        botId={botId}
        initialRules={null}
        initialAgentConfig={{}}
        disabled={false}
      />
    </div>
  );
}
