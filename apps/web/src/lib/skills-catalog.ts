export type SkillCategory = "productivity";

export interface SkillDef {
  id: string;
  /** Slash command aliases. First entry is the primary display name. */
  commands?: string[];
  category: SkillCategory;
  related?: string[];
}

export type CatalogSkill = SkillDef;

export interface CategoryMeta {
  id: SkillCategory;
  label: string;
  color: string;
}

export const CATEGORIES: CategoryMeta[] = [
  {
    id: "productivity",
    label: "Productivity",
    color: "text-cyan-700 bg-cyan-50 border-cyan-200",
  },
];

/** Bundled generic OSS skills that are available without hosted SaaS integrations. */
export const SKILLS: SkillDef[] = [
  {
    id: "brainstorming",
    commands: ["brainstorm"],
    category: "productivity",
    related: ["writing-plans", "using-superpowers"],
  },
  {
    id: "dispatching-parallel-agents",
    commands: ["parallel-agents"],
    category: "productivity",
    related: ["subagent-driven-development"],
  },
  {
    id: "executing-plans",
    commands: ["execute-plan"],
    category: "productivity",
    related: ["writing-plans"],
  },
  {
    id: "finishing-a-development-branch",
    commands: ["finish-branch"],
    category: "productivity",
    related: ["requesting-code-review", "using-git-worktrees"],
  },
  {
    id: "receiving-code-review",
    commands: ["receive-review"],
    category: "productivity",
    related: ["requesting-code-review"],
  },
  {
    id: "requesting-code-review",
    commands: ["request-review"],
    category: "productivity",
    related: ["receiving-code-review", "finishing-a-development-branch"],
  },
  {
    id: "subagent-driven-development",
    commands: ["subagent-dev"],
    category: "productivity",
    related: ["dispatching-parallel-agents"],
  },
  {
    id: "systematic-debugging",
    commands: ["debug"],
    category: "productivity",
    related: ["test-driven-development", "verification-before-completion"],
  },
  {
    id: "test-driven-development",
    commands: ["tdd"],
    category: "productivity",
    related: ["systematic-debugging"],
  },
  {
    id: "using-git-worktrees",
    commands: ["worktree"],
    category: "productivity",
    related: ["subagent-driven-development", "finishing-a-development-branch"],
  },
  {
    id: "using-superpowers",
    commands: ["superpowers"],
    category: "productivity",
    related: ["brainstorming", "systematic-debugging", "writing-plans"],
  },
  {
    id: "verification-before-completion",
    commands: ["verify"],
    category: "productivity",
    related: ["systematic-debugging", "test-driven-development"],
  },
  {
    id: "writing-plans",
    commands: ["write-plan"],
    category: "productivity",
    related: ["brainstorming", "executing-plans"],
  },
  {
    id: "writing-skills",
    commands: ["write-skill"],
    category: "productivity",
    related: ["using-superpowers"],
  },
];

/** Skills that should not be hidden from generic OSS slash suggestions. */
export const CORE_SKILLS = new Set(SKILLS.map((skill) => skill.id));

export type PurposeCategory = "assistant" | "general";

export interface PurposeMeta {
  id: PurposeCategory;
  label: string;
  emoji: string;
  descriptionKey: string;
}

export const PURPOSE_OPTIONS: PurposeMeta[] = [
  { id: "assistant", label: "purposeAssistant", emoji: "", descriptionKey: "purposeAssistantDesc" },
  { id: "general", label: "purposeGeneral", emoji: "", descriptionKey: "purposeGeneralDesc" },
];

export const PURPOSE_DISABLED_SKILLS: Record<PurposeCategory, string[]> = {
  assistant: [],
  general: [],
};
