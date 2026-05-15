"use client";

import SkillsCatalog from "./skills-catalog";

export default function SkillsPage() {
  return (
    <SkillsCatalog
      botId="local"
      initialDisabledSkills={[]}
      initialCustomSkills={[]}
    />
  );
}
