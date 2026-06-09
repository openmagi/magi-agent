"use client";

import { useParams } from "next/navigation";
import { CredentialsPanel } from "@/components/dashboard/credentials/credentials-panel";

export default function CredentialsPage() {
  const params = useParams<{ botId?: string | string[] }>();
  const rawBotId = params?.botId;
  const botId = Array.isArray(rawBotId) ? rawBotId[0] : rawBotId ?? "local";

  return <CredentialsPanel botId={botId} />;
}
