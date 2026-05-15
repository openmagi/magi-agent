export const CONSULTATION_AUDIO_MIME_TYPES = new Set([
  "audio/mpeg",
  "audio/mp3",
  "audio/mp4",
  "audio/x-m4a",
  "audio/wav",
  "audio/wave",
  "audio/x-wav",
  "audio/ogg",
  "audio/webm",
]);

export const CONSULTATION_AUDIO_EXTENSIONS = new Set([
  "mp3",
  "m4a",
  "wav",
  "ogg",
  "oga",
  "webm",
]);

const MIME_BY_EXTENSION: Record<string, string> = {
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  wav: "audio/wav",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  webm: "audio/webm",
};

export const CONSULTATION_JOB_STATUSES = [
  "pending",
  "transcribing",
  "postprocessing",
  "indexing",
  "completed",
  "failed",
  "cancelled",
] as const;

export type ConsultationJobStatus = (typeof CONSULTATION_JOB_STATUSES)[number];

export type ConsultationVerticalHint = "general" | "legal" | "accounting";

export const CONSULTATION_VERTICAL_HINTS = [
  "general",
  "legal",
  "accounting",
] as const;

export const DEFAULT_CONSULTATION_AUDIO_MAX_BYTES = 500 * 1024 * 1024;

export function getConsultationUploadExtension(name: string): string {
  return name.split(".").pop()?.toLowerCase() || "";
}

export function resolveConsultationAudioMimeType(file: {
  name: string;
  type?: string | null;
}): string {
  const type = file.type?.trim().toLowerCase() || "";
  if (type && type !== "application/octet-stream") return type;
  const ext = getConsultationUploadExtension(file.name);
  return MIME_BY_EXTENSION[ext] || type || "application/octet-stream";
}

export function isConsultationAudioMimeType(mimeType: string): boolean {
  return CONSULTATION_AUDIO_MIME_TYPES.has(mimeType.toLowerCase());
}

export function validateConsultationAudioFile(
  file: { name: string; type?: string | null; size: number },
  maxBytes = DEFAULT_CONSULTATION_AUDIO_MAX_BYTES,
): string | null {
  if (file.size > maxBytes) {
    return `Audio file exceeds ${Math.floor(maxBytes / (1024 * 1024))}MB limit`;
  }
  const mimeType = resolveConsultationAudioMimeType(file);
  if (!isConsultationAudioMimeType(mimeType)) {
    return `Unsupported audio file type: ${mimeType}`;
  }
  return null;
}

export function normalizeConsultationVerticalHint(value: unknown): ConsultationVerticalHint {
  const hint = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (hint === "legal" || hint === "law" || hint === "lawyer") return "legal";
  if (hint === "accounting" || hint === "tax" || hint === "accountant") {
    return "accounting";
  }
  return "general";
}
