"use client";

import { use } from "react";
import { CustomizeTab } from "@/components/dashboard/customize/customize-tab";

interface CustomizePageProps {
  params: Promise<{ botId: string }>;
}

export default function CustomizePage({ params }: CustomizePageProps) {
  const { botId } = use(params);
  return <CustomizeTab botId={botId} initialRules={null} />;
}
