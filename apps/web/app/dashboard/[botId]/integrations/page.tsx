"use client";

import { useParams } from "next/navigation";
import { IntegrationsManager } from "@/components/dashboard/integrations/integrations-manager";

export default function IntegrationsPage() {
  const params = useParams<{ botId?: string | string[] }>();
  const rawBotId = params?.botId;
  const botId = Array.isArray(rawBotId) ? rawBotId[0] : rawBotId ?? "local";

  return <IntegrationsManager botId={botId} />;
}
