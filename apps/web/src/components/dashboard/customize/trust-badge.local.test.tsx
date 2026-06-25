import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { TrustBadge } from "./trust-badge";

const src = readFileSync(
  new URL("./trust-badge.tsx", import.meta.url),
  "utf8",
);

describe("TrustBadge — shared trust-class badge primitive", () => {
  it("exports the six-trust-class taxonomy union (deterministic/advisory/hybrid/preview/mutator/operator_defined)", () => {
    expect(src).toContain("deterministic");
    expect(src).toContain("advisory");
    expect(src).toContain("hybrid");
    expect(src).toContain("preview");
    // PR-F-MUT3 — fifth bucket for prompt_injection + output_rewrite
    // primitives. Forwarded from policy-model.ts where the literal lives.
    expect(src).toContain("mutator");
    // PR-F-EXEC3 — sixth bucket for shell_command + shell_check primitives.
    // Forwarded from policy-model.ts where the literal lives.
    expect(src).toContain("operator_defined");
    expect(src).toMatch(/export\s+type\s+TrustClass/);
  });

  it("renders an aria-label that names the trust class ('Trust class: <Bucket>')", () => {
    // F1 contract: the inline pill was tagged 'Trust class: Advisory'.
    // The shared primitive must preserve that screen-reader contract.
    expect(src).toContain("Trust class:");
  });

  it("ships the canonical amber Advisory palette (byte-equivalent swap)", () => {
    // Existing GuidancePanel pill used bg-amber-500/10 + text-amber-700.
    expect(src).toContain("bg-amber-500/10");
    expect(src).toContain("text-amber-700");
  });

  it("ships a distinct deterministic palette (emerald)", () => {
    expect(src).toContain("bg-emerald-500/10");
    expect(src).toContain("text-emerald-700");
  });

  it("ships a distinct hybrid palette so override / strip checks are recognizable", () => {
    expect(src).toMatch(/hybrid:\s*"[^"]*bg-/);
  });

  it("uses rounded-full + uppercase tracking-wide geometry (matches F1 visual contract)", () => {
    expect(src).toContain("rounded-full");
    expect(src).toContain("uppercase tracking-wide");
  });
});


describe("TrustBadge — runtime rendering of each variant", () => {
  it("renders the Deterministic variant with emerald palette + aria-label + visible label", () => {
    const html = renderToStaticMarkup(<TrustBadge trustClass="deterministic" />);
    expect(html).toContain("Deterministic");
    expect(html).toContain('aria-label="Trust class: Deterministic"');
    expect(html).toContain("bg-emerald-500/10");
    expect(html).toContain("text-emerald-700");
  });

  it("renders the Advisory variant with amber palette + aria-label + visible label", () => {
    const html = renderToStaticMarkup(<TrustBadge trustClass="advisory" />);
    expect(html).toContain("Advisory");
    expect(html).toContain('aria-label="Trust class: Advisory"');
    expect(html).toContain("bg-amber-500/10");
    expect(html).toContain("text-amber-700");
  });

  it("renders the Hybrid variant with its dedicated palette + aria-label + visible label", () => {
    const html = renderToStaticMarkup(<TrustBadge trustClass="hybrid" />);
    expect(html).toContain("Hybrid");
    expect(html).toContain('aria-label="Trust class: Hybrid"');
    // The hybrid palette must not collide with deterministic/advisory hues.
    expect(html).not.toContain("text-emerald-700");
    expect(html).not.toContain("text-amber-700");
  });

  it("renders the Preview variant for shipped-but-not-wired presets", () => {
    const html = renderToStaticMarkup(<TrustBadge trustClass="preview" />);
    expect(html).toContain("Preview");
    expect(html).toContain('aria-label="Trust class: Preview"');
  });

  it("uses the caller-supplied label override but preserves the canonical aria-label", () => {
    const html = renderToStaticMarkup(
      <TrustBadge trustClass="advisory" label="Soft guidance" />,
    );
    expect(html).toContain("Soft guidance");
    // aria-label still names the trust bucket (not the override) so screen
    // readers always hear the honest taxonomy term.
    expect(html).toContain('aria-label="Trust class: Advisory"');
  });

  it("appends caller className after the variant palette", () => {
    const html = renderToStaticMarkup(
      <TrustBadge trustClass="deterministic" className="ml-2 shrink-0" />,
    );
    expect(html).toContain("ml-2 shrink-0");
    expect(html).toContain("bg-emerald-500/10");
  });
});


// ---------------------------------------------------------------------------
// PR-F-MUT3 — Mutator variant (4th meaningful trust-class)
// ---------------------------------------------------------------------------


describe("TrustBadge — F-MUT3 mutator variant", () => {
  it("renders the Mutator variant with the amber-yellow palette + visible label", () => {
    const html = renderToStaticMarkup(<TrustBadge trustClass="mutator" />);
    expect(html).toContain("Mutator");
    expect(html).toContain('aria-label="Trust class: Mutator"');
    // PR-F-MUT3 — amber-yellow ramp distinct from advisory amber, deterministic
    // emerald, hybrid violet, and preview blue so an operator never confuses
    // a mutator policy for a passive critic.
    expect(html).toContain("bg-yellow-400/15");
    expect(html).toContain("text-yellow-900");
    // Negative: must NOT collide with advisory / deterministic hues.
    expect(html).not.toContain("text-emerald-700");
    expect(html).not.toContain("text-amber-700");
  });

  it("renders the canonical 'modifies traffic' tooltip via title attribute", () => {
    const html = renderToStaticMarkup(<TrustBadge trustClass="mutator" />);
    // Honesty: hovering the badge surfaces the explicit mutation warning so
    // the operator sees it before activating the rule. The exact wording
    // is the spec sentence — assert phrasing so the warning cannot silently
    // soften over time.
    expect(html).toContain("Modifies inbound or outbound traffic");
    expect(html).toContain(
      "Verify the mutation does not break downstream tools or the model reasoning",
    );
    expect(html).toMatch(/title="Modifies inbound or outbound traffic/);
  });

  it("non-mutator variants ship NO title attribute (back-compat byte-equivalence)", () => {
    // The existing four variants (deterministic / advisory / hybrid /
    // preview) never had a tooltip; F-MUT3 must not introduce one on those
    // variants or downstream a11y snapshots / CSS expectations regress.
    for (const tc of ["deterministic", "advisory", "hybrid", "preview"] as const) {
      const html = renderToStaticMarkup(<TrustBadge trustClass={tc} />);
      expect(html).not.toContain("title=");
    }
  });

  it("accepts a caller-supplied tooltip override (parity with label override)", () => {
    const html = renderToStaticMarkup(
      <TrustBadge
        trustClass="mutator"
        tooltip="This mutator only fires under the lab profile."
      />,
    );
    expect(html).toContain(
      'title="This mutator only fires under the lab profile."',
    );
    // aria-label still names the trust bucket (not the tooltip).
    expect(html).toContain('aria-label="Trust class: Mutator"');
  });
});


// ---------------------------------------------------------------------------
// PR-F-EXEC3 — Operator-defined variant (5th meaningful trust-class)
// ---------------------------------------------------------------------------


describe("TrustBadge — F-EXEC3 operator_defined variant", () => {
  it("renders the Operator-defined variant with the amber-red palette + visible label", () => {
    const html = renderToStaticMarkup(
      <TrustBadge trustClass="operator_defined" />,
    );
    expect(html).toContain("Operator-defined");
    expect(html).toContain('aria-label="Trust class: Operator-defined"');
    // PR-F-EXEC3 — amber-red ramp (amber-600 tint + amber-900 ink +
    // amber-700 ring) distinct from advisory amber, deterministic emerald,
    // hybrid violet, preview blue, and mutator yellow so an operator never
    // confuses an operator-defined shell script for any of the built-in
    // primitives. The palette is what tells the operator "magi did not
    // write this — verify the script body before activating".
    expect(html).toContain("bg-amber-600/15");
    expect(html).toContain("text-amber-900");
    expect(html).toContain("ring-amber-700/40");
    // Negative: must NOT collide with the other variants' hues.
    expect(html).not.toContain("text-emerald-700");
    expect(html).not.toContain("text-amber-700");
    expect(html).not.toContain("text-yellow-900");
    expect(html).not.toContain("text-violet-700");
    expect(html).not.toContain("text-blue-700");
  });

  it("renders the spec 'external script' tooltip via title attribute", () => {
    const html = renderToStaticMarkup(
      <TrustBadge trustClass="operator_defined" />,
    );
    // The exact spec sentence — assert phrasing so the warning cannot
    // silently soften over time. magi does NOT verify the script body and
    // the operator must be told so before activating.
    expect(html).toContain("External script authored by the operator");
    expect(html).toContain("magi does NOT verify the script");
    expect(html).toContain(
      "Confirm the command does what you expect before activating",
    );
    expect(html).toMatch(/title="External script authored by the operator/);
  });

  it("renders the Terminal icon glyph inline beside the visible label", () => {
    const html = renderToStaticMarkup(
      <TrustBadge trustClass="operator_defined" />,
    );
    // The Terminal icon is the only inline glyph on the badge — its
    // presence is the visual cue that the rule body lives outside magi.
    // Pinned via data-testid so the assertion does not break on a future
    // lucide-react SVG markup change.
    expect(html).toContain(
      'data-testid="trust-badge-icon-operator-defined"',
    );
    // The label still renders next to the icon.
    expect(html).toContain("Operator-defined");
  });

  it("non-operator_defined variants ship NO Terminal icon (back-compat byte-equivalence)", () => {
    // The other five variants (deterministic / advisory / hybrid / preview /
    // mutator) never carried a glyph; F-EXEC3 must not introduce one on
    // those variants or downstream a11y snapshots / CSS expectations
    // regress.
    for (const tc of [
      "deterministic",
      "advisory",
      "hybrid",
      "preview",
      "mutator",
    ] as const) {
      const html = renderToStaticMarkup(<TrustBadge trustClass={tc} />);
      expect(html).not.toContain("trust-badge-icon-operator-defined");
    }
  });

  it("accepts a caller-supplied tooltip override (parity with label override)", () => {
    const html = renderToStaticMarkup(
      <TrustBadge
        trustClass="operator_defined"
        tooltip="This shell hook is sandboxed to a /tmp dir."
      />,
    );
    expect(html).toContain(
      'title="This shell hook is sandboxed to a /tmp dir."',
    );
    // aria-label still names the trust bucket (not the tooltip).
    expect(html).toContain('aria-label="Trust class: Operator-defined"');
  });
});
