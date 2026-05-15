import type { ConsultationVerticalHint } from "./formats";

export type ConsultationTranscriptSegment = {
  speaker: string;
  startSeconds: number;
  endSeconds: number;
  text: string;
};

export type BuildTranscriptMarkdownInput = {
  sourceFilename: string;
  processedAt: string;
  backend: string;
  durationSeconds?: number | null;
  warnings?: string[];
  segments: ConsultationTranscriptSegment[];
};

export type BuildConsultationMemoMarkdownInput = {
  sourceFilename: string;
  verticalHint: ConsultationVerticalHint;
  generatedAt: string;
  summary: string[];
  keyIssues: string[];
  clientRequests: string[];
  neededMaterials: string[];
  followUpQuestions: string[];
  nextActions: string[];
  deadlinesAndDates: string[];
  risksAndCaveats: string[];
  sourceNotes: string[];
};

export type ConsultationTask = {
  title: string;
  ownerHint: "bot" | "user" | "client" | "team" | "unknown";
  dueDate: string | null;
  sourceTimestamp: string | null;
  confidence: "low" | "medium" | "high";
};

export type ConsultationTasksJson = {
  tasks: Array<{
    title: string;
    owner_hint: ConsultationTask["ownerHint"];
    due_date: string | null;
    source_timestamp: string | null;
    confidence: ConsultationTask["confidence"];
  }>;
};

function formatTimestamp(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainingSeconds = safeSeconds % 60;
  return [hours, minutes, remainingSeconds]
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "unknown";
  const safeSeconds = Math.floor(seconds);
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainingSeconds = safeSeconds % 60;
  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  if (remainingSeconds > 0 || parts.length === 0) parts.push(`${remainingSeconds}s`);
  return parts.join(" ");
}

function appendBulletSection(lines: string[], title: string, values: string[]): void {
  lines.push("", `## ${title}`);
  if (values.length === 0) {
    lines.push("- None identified");
    return;
  }
  for (const value of values) {
    const trimmed = value.trim();
    if (trimmed) lines.push(`- ${trimmed}`);
  }
}

export function buildTranscriptMarkdown(input: BuildTranscriptMarkdownInput): string {
  const lines = [
    "# Consultation Transcript",
    "",
    `- Source file: ${input.sourceFilename}`,
    `- Processed at: ${input.processedAt}`,
    `- Backend: ${input.backend}`,
    `- Duration: ${formatDuration(input.durationSeconds)}`,
  ];

  if (input.warnings?.length) {
    lines.push("", "## Warnings");
    for (const warning of input.warnings) {
      const trimmed = warning.trim();
      if (trimmed) lines.push(`- ${trimmed}`);
    }
  }

  lines.push("", "## Transcript");
  if (input.segments.length === 0) {
    lines.push("_No speech segments were produced._");
  } else {
    for (const segment of input.segments) {
      const start = formatTimestamp(segment.startSeconds);
      const end = formatTimestamp(segment.endSeconds);
      lines.push(`[${start}-${end}] ${segment.speaker}: ${segment.text.trim()}`);
    }
  }

  return `${lines.join("\n")}\n`;
}

export function buildConsultationMemoMarkdown(
  input: BuildConsultationMemoMarkdownInput,
): string {
  const lines = [
    "# Consultation Memo",
    "",
    `- Source file: ${input.sourceFilename}`,
    `- Generated at: ${input.generatedAt}`,
    `- Vertical hint: ${input.verticalHint}`,
    "- Notice: AI draft for professional review before client delivery.",
  ];

  appendBulletSection(lines, "Summary", input.summary);
  appendBulletSection(lines, "Key Issues", input.keyIssues);
  appendBulletSection(lines, "Client Requests", input.clientRequests);
  appendBulletSection(lines, "Needed Materials", input.neededMaterials);
  appendBulletSection(lines, "Follow-up Questions", input.followUpQuestions);
  appendBulletSection(lines, "Next Actions", input.nextActions);
  appendBulletSection(lines, "Deadlines and Dates", input.deadlinesAndDates);
  appendBulletSection(lines, "Risks and Caveats", input.risksAndCaveats);
  appendBulletSection(lines, "Source Notes", input.sourceNotes);

  return `${lines.join("\n")}\n`;
}

export function buildConsultationTasksJson(
  tasks: ConsultationTask[],
): ConsultationTasksJson {
  return {
    tasks: tasks.map((task) => ({
      title: task.title,
      owner_hint: task.ownerHint,
      due_date: task.dueDate,
      source_timestamp: task.sourceTimestamp,
      confidence: task.confidence,
    })),
  };
}
