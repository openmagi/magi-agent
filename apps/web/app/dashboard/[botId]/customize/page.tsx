"use client";

import { useParams } from "next/navigation";
import { CustomizeRuntimeConsole } from "@/components/dashboard/customize/customize-tab";

export default function CustomizePage() {
  const params = useParams<{ botId?: string | string[] }>();
  const rawBotId = params?.botId;
  const botId = Array.isArray(rawBotId) ? rawBotId[0] : rawBotId ?? "local";

  return <CustomizeRuntimeConsole botId={botId} />;
}
