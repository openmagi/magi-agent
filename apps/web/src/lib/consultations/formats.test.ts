import { describe, expect, it } from "vitest";

import {
  CONSULTATION_AUDIO_EXTENSIONS,
  CONSULTATION_AUDIO_MIME_TYPES,
  normalizeConsultationVerticalHint,
  resolveConsultationAudioMimeType,
  validateConsultationAudioFile,
} from "./formats";

describe("consultation audio formats", () => {
  it("accepts common consultation recording MIME types", () => {
    expect(CONSULTATION_AUDIO_MIME_TYPES.has("audio/mpeg")).toBe(true);
    expect(CONSULTATION_AUDIO_MIME_TYPES.has("audio/mp4")).toBe(true);
    expect(CONSULTATION_AUDIO_MIME_TYPES.has("audio/wav")).toBe(true);
    expect(CONSULTATION_AUDIO_MIME_TYPES.has("audio/ogg")).toBe(true);
    expect(CONSULTATION_AUDIO_MIME_TYPES.has("audio/webm")).toBe(true);
  });

  it("accepts common audio extensions", () => {
    expect(CONSULTATION_AUDIO_EXTENSIONS.has("mp3")).toBe(true);
    expect(CONSULTATION_AUDIO_EXTENSIONS.has("m4a")).toBe(true);
    expect(CONSULTATION_AUDIO_EXTENSIONS.has("wav")).toBe(true);
    expect(CONSULTATION_AUDIO_EXTENSIONS.has("ogg")).toBe(true);
    expect(CONSULTATION_AUDIO_EXTENSIONS.has("webm")).toBe(true);
  });

  it("resolves octet-stream uploads by extension", () => {
    expect(resolveConsultationAudioMimeType({
      name: "client-call.M4A",
      type: "application/octet-stream",
    })).toBe("audio/mp4");
  });

  it("rejects unsupported files", () => {
    expect(validateConsultationAudioFile({
      name: "memo.pdf",
      type: "application/pdf",
      size: 1024,
    })).toContain("Unsupported audio file type");
  });

  it("enforces size limits", () => {
    expect(validateConsultationAudioFile({
      name: "call.mp3",
      type: "audio/mpeg",
      size: 11,
    }, 10)).toBe("Audio file exceeds 0MB limit");
  });
});

describe("consultation vertical hints", () => {
  it("normalizes supported hints", () => {
    expect(normalizeConsultationVerticalHint("legal")).toBe("legal");
    expect(normalizeConsultationVerticalHint("law")).toBe("legal");
    expect(normalizeConsultationVerticalHint("accounting")).toBe("accounting");
    expect(normalizeConsultationVerticalHint("tax")).toBe("accounting");
    expect(normalizeConsultationVerticalHint("general")).toBe("general");
  });

  it("falls back to general for unknown hints", () => {
    expect(normalizeConsultationVerticalHint("medical")).toBe("general");
    expect(normalizeConsultationVerticalHint(null)).toBe("general");
  });
});
