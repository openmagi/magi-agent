// Catalog-vs-truth check: the visible BASE_MODEL_OPTIONS labels must match the
// runtime model id the slug actually maps to (chat-core/channel-model-selection
// `MODEL_SELECTION_TO_RUNTIME_MODEL`). The chat picker briefly shipped "Claude
// Sonnet 4.5" / "Claude Opus 4.6" while the slugs routed to claude-sonnet-4-6 /
// claude-opus-4-8, so the label lied to the user about which model they were
// about to call. We can't import BASE_MODEL_OPTIONS here (it pulls in
// `@/lib/models/local-llm`, an alias vitest doesn't resolve), so verify by
// reading the source file directly — that pins the relationship without
// dragging the alias chain into a unit test.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  join(import.meta.dirname, "model-options.ts"),
  "utf-8",
);

describe("BASE_MODEL_OPTIONS label fidelity", () => {
  it("labels 'sonnet' as Claude Sonnet 5 (slug routes to claude-sonnet-5)", () => {
    expect(source).toMatch(/value: "sonnet",\s*label: "Claude Sonnet 5"/);
    expect(source).not.toMatch(/value: "sonnet",\s*label: "Claude Sonnet 4\.6"/);
    expect(source).not.toMatch(/value: "sonnet",\s*label: "Claude Sonnet 4\.5"/);
  });

  it("labels 'opus' as Claude Opus 4.8 (slug routes to claude-opus-4-8)", () => {
    expect(source).toMatch(/value: "opus",\s*label: "Claude Opus 4\.8"/);
    expect(source).not.toMatch(/value: "opus",\s*label: "Claude Opus 4\.6"/);
  });

  it("labels 'haiku' as Claude Haiku 4.5 (slug routes to claude-haiku-4-5)", () => {
    expect(source).toMatch(/value: "haiku",\s*label: "Claude Haiku 4\.5"/);
  });

  it("offers Gemini 3.5 Flash (slug routes to google/gemini-3.5-flash)", () => {
    // Gemini Flash default bumped 3.1 → 3.5; the chat picker must surface it
    // or users can only reach 3.5 by typing the env knob.
    expect(source).toMatch(
      /value: "gemini_3_5_flash",\s*label: "Gemini 3\.5 Flash \(Google\)"/,
    );
  });
});
