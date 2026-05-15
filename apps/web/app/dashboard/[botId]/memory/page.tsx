"use client";

import { use } from "react";
import { MemoryEditor } from "./memory-client";

interface MemoryPageProps {
  params: Promise<{ botId: string }>;
}

export default function MemoryPage({ params }: MemoryPageProps) {
  const { botId } = use(params);
  return <MemoryEditor botId={botId} botName="Local Agent" botOnline={true} />;
}
