import { SKILLS } from "@/lib/skills-catalog";

const CUSTOM_PREFIX = "custom-";
const MAX_TITLE_CHARS = 80;
const MAX_DESCRIPTION_CHARS = 220;
const MAX_BODY_CHARS = 12_000;
const MAX_TAGS = 8;
const TAG_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

const RESERVED_SKILL_IDS = new Set(SKILLS.map((skill) => skill.id));

export interface CustomSkillInput {
  title: string;
  description: string;
  body: string;
  tags: string[];
}

export interface CustomSkillListItem {
  id: string;
  name: string;
  title: string;
  description: string;
  body: string;
  tags: string[];
  status: "installed";
  createdAt: string;
  reviewedAt: string | null;
}

export interface CustomSkillRow {
  id: string;
  skill_name: string;
  content: string;
  status: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export function normalizeCustomSkillName(title: string): string {
  const cleanTitle = title.trim();
  const normalized = title
    .trim()
    .toLowerCase()
    .replace(/^custom-+/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_TITLE_CHARS);
  if (normalized) return `${CUSTOM_PREFIX}${normalized}`;
  return `${CUSTOM_PREFIX}skill-${stableShortHash(cleanTitle || "skill")}`;
}

export function customSkillPathKey(skillName: string): string {
  return `${normalizeCustomSkillName(skillName)}__SKILL.md`;
}

export function validateCustomSkillInput(raw: unknown): CustomSkillInput {
  const input = asRecord(raw);
  const title = stringField(input.title).slice(0, MAX_TITLE_CHARS).trim();
  const description = stringField(input.description)
    .slice(0, MAX_DESCRIPTION_CHARS)
    .trim();
  const body = stringField(input.body).trim();
  const tags = arrayField(input.tags)
    .map((tag) => stringField(tag).trim().toLowerCase())
    .filter((tag) => TAG_RE.test(tag))
    .slice(0, MAX_TAGS);

  if (title.length < 2) {
    throw new Error("Custom skill title is required");
  }
  const skillName = normalizeCustomSkillName(title);
  const unprefixed = skillName.slice(CUSTOM_PREFIX.length);
  if (RESERVED_SKILL_IDS.has(skillName) || RESERVED_SKILL_IDS.has(unprefixed)) {
    throw new Error(`Custom skill name is reserved: ${unprefixed}`);
  }
  if (description.length < 8) {
    throw new Error("Custom skill description is required");
  }
  if (body.length < 8) {
    throw new Error("Custom skill instructions are required");
  }
  if (body.length > MAX_BODY_CHARS) {
    throw new Error(`Custom skill instructions are too long (${MAX_BODY_CHARS} max)`);
  }

  return { title, description, body, tags: [...new Set(tags)] };
}

export function buildCustomSkillContent(input: CustomSkillInput): string {
  const name = normalizeCustomSkillName(input.title);
  const description = normalizeDescription(input.description);
  const tags = input.tags.length
    ? input.tags.map((tag) => `  - ${yamlString(tag)}`).join("\n")
    : "  - custom";

  return [
    "---",
    `name: ${yamlString(name)}`,
    `description: ${yamlString(description)}`,
    "kind: prompt",
    "permission: meta",
    "tags:",
    tags,
    "---",
    "",
    `# ${input.title}`,
    "",
    "## Instructions",
    "",
    input.body.trim(),
    "",
  ].join("\n");
}

export function parseCustomSkillRow(row: CustomSkillRow): CustomSkillListItem {
  const parsed = parseSkillMdLite(row.content);
  const isCustom = row.skill_name.startsWith("custom-");
  return {
    id: row.id,
    name: isCustom ? normalizeCustomSkillName(row.skill_name) : row.skill_name,
    title: titleFromContent(row.content, row.skill_name),
    description: parsed.description || "",
    body: bodyFromContent(row.content),
    tags: parsed.tags,
    status: "installed",
    createdAt: row.created_at,
    reviewedAt: row.reviewed_at,
  };
}

export function normalizeDescription(description: string): string {
  const clean = description.trim().replace(/\s+/g, " ");
  const lower = clean.toLowerCase();
  if (lower.startsWith("use ") || lower.startsWith("review ") || lower.startsWith("write ")) {
    return sentence(clean);
  }
  return sentence(`Use this skill to ${clean.charAt(0).toLowerCase()}${clean.slice(1)}`);
}

function parseSkillMdLite(content: string): { description: string; tags: string[] } {
  const frontmatter = content.match(/^---\n([\s\S]*?)\n---/);
  const yaml = frontmatter?.[1] ?? "";
  const description =
    yaml.match(/^description:\s*"?(.+?)"?\s*$/m)?.[1]?.trim() ?? "";
  const tagsBlock = yaml.match(/^tags:\n((?:\s+-\s+.+\n?)+)/m)?.[1] ?? "";
  const tags = tagsBlock
    .split("\n")
    .map((line) => line.trim().replace(/^-\s+/, "").replace(/^"|"$/g, ""))
    .filter(Boolean);
  return { description, tags };
}

function titleFromContent(content: string, fallback: string): string {
  const heading = content.match(/^#\s+(.+)$/m)?.[1]?.trim();
  if (heading) return heading;
  return fallback
    .replace(/^custom-/, "")
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function bodyFromContent(content: string): string {
  const marker = "## Instructions";
  const idx = content.indexOf(marker);
  if (idx < 0) return content.replace(/^---\n[\s\S]*?\n---\n?/, "").trim();
  return content.slice(idx + marker.length).trim();
}

function sentence(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function yamlString(value: string): string {
  if (/^[a-z0-9_-]+$/i.test(value)) return value;
  if (/^[A-Za-z0-9][A-Za-z0-9 ,.'()/-]*[.!?]?$/.test(value)) return value;
  return JSON.stringify(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function arrayField(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stableShortHash(value: string): string {
  let hash = 0x811c9dc5;
  for (const char of value.normalize("NFKC")) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36).padStart(6, "0").slice(0, 6);
}
