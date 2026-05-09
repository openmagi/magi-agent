export type LongTermMemoryPolicy = "normal" | "background_only" | "disabled";

export interface SourceAuthorityRecord {
  turnId: string;
  currentSourceKinds: string[];
  longTermMemoryPolicy: LongTermMemoryPolicy;
  classifierReason: string;
  recordedAt?: number;
}

export interface SourceAuthorityResolutionInput {
  classifierPolicy: LongTermMemoryPolicy;
  classifierCurrentSourcesAuthoritative: boolean;
  currentSourceKinds: readonly string[];
}

export interface SourceAuthorityPromptInput {
  turnId: string;
  currentSourceKinds: readonly string[];
  longTermMemoryPolicy: LongTermMemoryPolicy;
  classifierReason: string;
}

const CURRENT_TURN_SOURCE_RE =
  /<current-turn-source\b[^>]*\bkind\s*=\s*["']([^"']+)["'][^>]*>/gi;

function normalizeKind(value: string): string | null {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  return normalized.length > 0 ? normalized.slice(0, 80) : null;
}

function extractSourceKinds(text: string): string[] {
  const kinds: string[] = [];
  for (const match of text.matchAll(CURRENT_TURN_SOURCE_RE)) {
    const kind = match[1] ? normalizeKind(match[1]) : null;
    if (kind && !kinds.includes(kind)) kinds.push(kind);
  }
  return kinds;
}

export function detectCurrentTurnSourceKinds(input: {
  system?: string;
  userText?: string;
  hasImages?: boolean;
}): string[] {
  const kinds = [
    ...extractSourceKinds(input.system ?? ""),
    ...extractSourceKinds(input.userText ?? ""),
  ];
  if (input.hasImages === true && !kinds.includes("image")) {
    kinds.push("image");
  }
  return [...new Set(kinds)];
}

export function resolveEffectiveLongTermMemoryPolicy(
  input: SourceAuthorityResolutionInput,
): LongTermMemoryPolicy {
  if (input.classifierPolicy === "disabled") return "disabled";
  if (input.classifierPolicy === "background_only") return "background_only";
  if (input.classifierCurrentSourcesAuthoritative) return "background_only";
  if (input.currentSourceKinds.length > 0) return "background_only";
  return "normal";
}

export function buildSourceAuthorityPromptBlock(input: SourceAuthorityPromptInput): string {
  const currentSources =
    input.currentSourceKinds.length > 0
      ? input.currentSourceKinds.join(", ")
      : "(none)";
  return [
    `<source_authority_contract hidden="true" turn="${input.turnId}">`,
    "Authority order:",
    "- L0 latest_user_message: highest authority for this turn.",
    "- L1 current_turn_sources: user-selected KB, current attachments, current images, and current system addenda.",
    "- L2 current_session_transcript: recent conversation state.",
    "- L3 runtime_state: task boards, tool evidence, route metadata, and current channel state.",
    "- L4 long_term_memory: Hipocampus root/qmd/session memory; reference only.",
    `current_turn_sources: ${currentSources}`,
    `long_term_memory_policy: ${input.longTermMemoryPolicy}`,
    `classifier_reason: ${input.classifierReason}`,
    "Rules:",
    "- If L0/L1 conflicts with L4, follow L0/L1.",
    "- If long_term_memory_policy is disabled, do not use long-term memory as evidence.",
    "- If long_term_memory_policy is background_only, long-term memory may only provide passive background and must not decide, replace, or reinterpret the current source.",
    "</source_authority_contract>",
  ].join("\n");
}

export function buildSourceAuthorityClassifierContext(input: {
  records: readonly SourceAuthorityRecord[];
  memoryPhrases: readonly string[];
}): string {
  if (input.records.length === 0 && input.memoryPhrases.length === 0) return "";
  const lines = ["Source authority context:"];
  for (const record of input.records) {
    lines.push(
      [
        `turn=${record.turnId}`,
        `current_sources=${record.currentSourceKinds.join(",") || "none"}`,
        `long_term_memory_policy=${record.longTermMemoryPolicy}`,
        `reason=${record.classifierReason}`,
      ].join(" | "),
    );
  }
  if (input.memoryPhrases.length > 0) {
    lines.push(`recalled_memory_phrases=${input.memoryPhrases.join(" ; ")}`);
  }
  return lines.join("\n");
}
