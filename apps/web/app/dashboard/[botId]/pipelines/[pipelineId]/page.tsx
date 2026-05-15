"use client";

import { use } from "react";
import PipelineDetail from "./pipeline-detail";

interface PipelineDetailPageProps {
  params: Promise<{ botId: string; pipelineId: string }>;
}

export default function PipelineDetailPage({ params }: PipelineDetailPageProps) {
  const { botId, pipelineId } = use(params);
  return <PipelineDetail botId={botId} botName="Local Agent" pipelineId={pipelineId} />;
}
