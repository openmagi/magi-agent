"use client";

import { use } from "react";
import SkillsCatalog from "./skills-catalog";

interface SkillsPageProps {
  params: Promise<{ botId: string }>;
}

export default function SkillsPage({ params }: SkillsPageProps) {
  const { botId } = use(params);
  return <SkillsCatalog botId={botId} initialDisabledSkills={[]} initialCustomSkills={[]} />;
}
