"use client";

import { use } from "react";
import PipelinesList from "./pipelines-list";

interface PipelinesPageProps {
  params: Promise<{ botId: string }>;
}

export default function PipelinesPage({ params }: PipelinesPageProps) {
  const { botId } = use(params);
  return <PipelinesList botId={botId} botName="Local Agent" />;
}
