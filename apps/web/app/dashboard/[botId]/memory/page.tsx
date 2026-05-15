"use client";

import { MemoryEditor } from "./memory-client";

export default function MemoryPage() {
  return <MemoryEditor botId="local" botName="Local Agent" botOnline={true} />;
}
