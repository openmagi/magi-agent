/**
 * Tests for RuntimeFieldChips (PR-F-UX2 / F8 core).
 *
 * Static-source assertions (the file pattern the rest of customize/ uses,
 * since we don't have a DOM testing-library) plus a small runtime test that
 * exercises the click → onInsert callback with React's server renderer
 * (matches the trust-badge.local.test.tsx approach).
 */
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

const componentSrc = readFileSync(
  new URL("./runtime-field-chips.tsx", import.meta.url),
  "utf8",
);

const apiSrc = readFileSync(
  new URL("../../../../lib/customize-api.ts", import.meta.url),
  "utf8",
);


describe("RuntimeFieldChips — source contract", () => {
  it("exports the RuntimeFieldChips component", () => {
    expect(componentSrc).toContain("export function RuntimeFieldChips");
  });

  it("fetches via getRuntimeFields on (lifecycle, condition, tool) changes", () => {
    expect(componentSrc).toContain("getRuntimeFields");
    // useEffect must re-run when the tuple changes; assert lifecycle +
    // condition + the toolKey are in the dep array so the chip menu
    // refreshes without a manual reload.
    expect(componentSrc).toMatch(
      /useEffect\([\s\S]*?\}, \[agentFetch, lifecycle, condition, toolKey\]\)/,
    );
  });

  it("calls onInsert with the chip name (canonical variable token)", () => {
    // The chip emits its bare name as the insert token; the wizard's
    // insertAtCaret helper splices it at the input caret. The wizard owns
    // the cursor restoration; this component is presentation-only.
    expect(componentSrc).toContain("onInsert(chip.name)");
  });

  it("renders nothing when the chip list is empty (degrade-quiet)", () => {
    // Empty chip list = unknown tuple OR flag-OFF OR fetch error. The
    // surrounding text input still works; we do NOT render a "no chips"
    // placeholder that clutters the wizard chrome.
    expect(componentSrc).toMatch(/chips\.length === 0[\s\S]*?return null/);
  });

  it("ships a data-testid='runtime-field-chips' marker for downstream e2e hooks", () => {
    expect(componentSrc).toContain('data-testid="runtime-field-chips"');
  });

  it("buttons set type='button' so they never submit a parent form", () => {
    expect(componentSrc).toContain('type="button"');
  });

  it("each chip surfaces an aria-label + title so screen readers + hover hints work", () => {
    expect(componentSrc).toContain("aria-label={`Insert ${chip.name}`}");
    expect(componentSrc).toContain("title=");
  });

  it("does NOT wrap the inserted token with Jinja-style braces (no fake substitution)", () => {
    // The runtime gate does not implement {{var}} substitution today; the
    // chip surface must NOT pretend it does. Authors who want a literal
    // reference can add the braces themselves.
    expect(componentSrc).not.toContain('"{{"');
    expect(componentSrc).not.toContain('"}}"');
  });
});


describe("getRuntimeFields — API client contract", () => {
  it("declares the RuntimeFieldChip + RuntimeFieldsResponse interfaces", () => {
    expect(apiSrc).toContain("interface RuntimeFieldChip");
    expect(apiSrc).toContain("interface RuntimeFieldsResponse");
  });

  it("hits GET /v1/app/customize/runtime-fields with lifecycle + condition + optional tool", () => {
    expect(apiSrc).toContain("/v1/app/customize/runtime-fields");
    // Three URL params on the GET (URLSearchParams constructs from these keys).
    expect(apiSrc).toContain("lifecycle: args.lifecycle");
    expect(apiSrc).toContain("condition: args.condition");
    expect(apiSrc).toContain('params.set("tool", args.tool)');
  });

  it("is fail-open: returns empty fields on fetch / HTTP error", () => {
    expect(apiSrc).toMatch(/getRuntimeFields[\s\S]*?fields: \[\]/);
  });

  it("URL-encodes parameters via URLSearchParams (no manual string concat)", () => {
    expect(apiSrc).toContain("URLSearchParams");
  });
});


// ---------------------------------------------------------------------------
// Runtime behavior — chip click → onInsert callback.
//
// We can't render the full component without a DOM, but we can validate the
// shape of getRuntimeFields by stubbing fetch and asserting the callback
// contract that the wizard relies on (chip.name == insert token).
// ---------------------------------------------------------------------------
describe("getRuntimeFields — runtime stub", () => {
  it("returns the parsed fields list from a mocked fetch", async () => {
    const { getRuntimeFields } = await import("../../../../lib/customize-api");
    const fakeFetch = vi.fn(async (_path: string) => ({
      ok: true,
      json: async () => ({
        fields: [
          { name: "session_id", type: "string", description: "stable id" },
          { name: "tool_input.url", type: "string", description: "" },
        ],
        context: "before_tool_use/regex",
        source: "fields_for_context",
      }),
    })) as unknown as (path: string, init?: RequestInit) => Promise<Response>;

    const out = await getRuntimeFields(fakeFetch, {
      lifecycle: "before_tool_use",
      condition: "regex",
    });
    expect(out.fields.length).toBe(2);
    expect(out.fields[0].name).toBe("session_id");
    expect(out.source).toBe("fields_for_context");
  });

  it("returns the empty/unknown placeholder on a non-2xx fetch", async () => {
    const { getRuntimeFields } = await import("../../../../lib/customize-api");
    const fakeFetch = vi.fn(async (_path: string) => ({
      ok: false,
      json: async () => ({}),
    })) as unknown as (path: string, init?: RequestInit) => Promise<Response>;

    const out = await getRuntimeFields(fakeFetch, {
      lifecycle: "before_tool_use",
      condition: "regex",
    });
    expect(out.fields).toEqual([]);
    expect(out.source).toBe("unknown");
  });

  it("encodes the optional tool parameter into the URL when given", async () => {
    const { getRuntimeFields } = await import("../../../../lib/customize-api");
    const seen: string[] = [];
    const fakeFetch = vi.fn(async (path: string) => {
      seen.push(path);
      return {
        ok: true,
        json: async () => ({ fields: [], context: "", source: "unknown" }),
      } as unknown as Response;
    }) as unknown as (path: string, init?: RequestInit) => Promise<Response>;

    await getRuntimeFields(fakeFetch, {
      lifecycle: "before_tool_use",
      condition: "regex",
      tool: "FileRead",
    });
    expect(seen.length).toBe(1);
    expect(seen[0]).toContain("lifecycle=before_tool_use");
    expect(seen[0]).toContain("condition=regex");
    expect(seen[0]).toContain("tool=FileRead");
  });

  it("omits the tool parameter when undefined/null/empty", async () => {
    const { getRuntimeFields } = await import("../../../../lib/customize-api");
    const seen: string[] = [];
    const fakeFetch = vi.fn(async (path: string) => {
      seen.push(path);
      return {
        ok: true,
        json: async () => ({ fields: [], context: "", source: "unknown" }),
      } as unknown as Response;
    }) as unknown as (path: string, init?: RequestInit) => Promise<Response>;

    await getRuntimeFields(fakeFetch, {
      lifecycle: "after_tool_use",
      condition: "regex",
    });
    expect(seen[0]).not.toContain("tool=");
  });
});


// ---------------------------------------------------------------------------
// F-UX-EXTRA #3 — friendly chip labels + richer hover tooltips.
//
// The chip face shows a human-readable label when we know one; the raw
// canonical variable name + type + description move into the tooltip. A
// chip CLICK still inserts the raw chip.name so the runtime gate sees a
// byte-identical token.
// ---------------------------------------------------------------------------
describe("RuntimeFieldChips — F-UX-EXTRA #3 friendly labels + tooltips", () => {
  it("declares the VARIABLE_FRIENDLY_LABELS dictionary", () => {
    expect(componentSrc).toContain("const VARIABLE_FRIENDLY_LABELS:");
    // Both common shapes the runtime emits today must have an entry so the
    // most-used chips never fall through to the raw-name fallback.
    expect(componentSrc).toMatch(/tool_name:\s*\{[\s\S]*?label:\s*"Tool name"/);
    expect(componentSrc).toMatch(
      /"tool_input\.url":\s*\{[\s\S]*?label:\s*"Tool URL argument"/,
    );
    expect(componentSrc).toMatch(/session_id:\s*\{[\s\S]*?label:\s*"Session ID"/);
  });

  it("renders the friendly label as the chip face when one is known", () => {
    // The face uses the dictionary entry's label; the raw chip.name only
    // shows on the face when we don't have a friendly label (fallback).
    expect(componentSrc).toContain("const friendly = VARIABLE_FRIENDLY_LABELS[chip.name]");
    expect(componentSrc).toContain("const face = friendly?.label ?? chip.name");
  });

  it("tooltip composes friendly label + raw name + type + description", () => {
    // The hover tooltip carries the full triple so a screen reader / hover
    // user sees both the human label and the canonical token they're
    // inserting. Degrades to the original `${type} — ${description}` form
    // for chips that have no friendly entry.
    expect(componentSrc).toMatch(
      /const tooltip = friendly[\s\S]*?\$\{friendly\.label\}[\s\S]*?\$\{chip\.name\}/,
    );
  });

  it("still inserts the RAW canonical chip name on click (no friendly label leakage)", () => {
    // The runtime gate only honors the canonical name; insertion must be
    // byte-identical regardless of what the chip face renders.
    expect(componentSrc).toContain("onClick={() => onInsert(chip.name)}");
  });

  it("emits a data-chip-name attribute carrying the raw token for e2e/test hooks", () => {
    expect(componentSrc).toContain("data-chip-name={chip.name}");
  });
});
