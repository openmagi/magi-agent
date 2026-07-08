/**
 * Source-string assertions for the wizard → NL handoff wiring
 * (PR-F-HANDOFF). The behavioural serializer is covered by
 * :file:`handoff.local.test.ts`; this file pins the wiring so a future
 * refactor cannot silently strip the chrome button or stop forwarding
 * the parent callback.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const wizardSrc = readFileSync(
  new URL("./author-wizard.tsx", import.meta.url),
  "utf8",
);

const chromeSrc = readFileSync(
  new URL("./wizard-chrome.tsx", import.meta.url),
  "utf8",
);

const guidedShellSrc = readFileSync(
  new URL("../guided-wizard.tsx", import.meta.url),
  "utf8",
);

const hubSrc = readFileSync(
  new URL("../customize-hub.tsx", import.meta.url),
  "utf8",
);

const nlSrc = readFileSync(
  new URL("../nl-rule-compose.tsx", import.meta.url),
  "utf8",
);


describe("WizardChrome — Continue-in-NL button (PR-F-HANDOFF)", () => {
  it("declares the onContinueInNl prop on WizardChromeProps", () => {
    expect(chromeSrc).toContain("onContinueInNl?:");
    expect(chromeSrc).toMatch(
      /onContinueInNl[\s\S]*?\(\) => void/,
    );
  });

  it("renders the Continue-in-NL button only when the callback is provided", () => {
    // Conditional render keeps the chrome usable by any caller that doesn't
    // wire the handoff (e.g. embedded tests).
    expect(chromeSrc).toContain("onContinueInNl ? (");
    expect(chromeSrc).toContain('data-testid="continue-in-nl-button"');
    expect(chromeSrc).toContain("Continue in NL");
  });

  it("uses a chat-bubble lucide icon for visual recognition", () => {
    expect(chromeSrc).toContain("MessageSquare");
  });

  it("wires the click handler to the prop callback", () => {
    expect(chromeSrc).toMatch(
      /onClick=\{onContinueInNl\}[\s\S]*?data-testid="continue-in-nl-button"/,
    );
  });
});


describe("AuthorWizard — wires onContinueInNl through to WizardChrome", () => {
  it("declares onContinueInNl on AuthorWizardProps", () => {
    expect(wizardSrc).toContain("onContinueInNl?:");
    expect(wizardSrc).toMatch(
      /onContinueInNl\?\: \(primer: string\) => void/,
    );
  });

  it("imports the handoff serializer", () => {
    expect(wizardSrc).toContain('from "./handoff"');
    expect(wizardSrc).toContain("serializeDraftToPrimer");
  });

  it("wraps the parent callback to inject the live draft + current step", () => {
    expect(wizardSrc).toContain("handleContinueInNl");
    expect(wizardSrc).toContain("serializeDraftToPrimer(");
    // The serializer must read both pieces of wizard state so the
    // primer carries the operator's last-known position.
    expect(wizardSrc).toMatch(/serializeDraftToPrimer\([\s\S]*?draft[\s\S]*?currentKey/);
  });

  it("forwards the handler to WizardChrome's onContinueInNl prop", () => {
    expect(wizardSrc).toMatch(
      /<WizardChrome[\s\S]*?onContinueInNl=\{handleContinueInNl\}/,
    );
  });

  it("renders the button on every step (handler not gated by step)", () => {
    // The handler is computed once outside the WizardChrome render and the
    // chrome itself renders the button on every step (it lives in the nav
    // row, not inside any step-specific body).
    expect(wizardSrc).not.toMatch(
      /currentKey === ".+"[\s\S]*?handleContinueInNl/,
    );
  });
});


describe("GuidedWizard shell — forwards the handoff prop", () => {
  it("declares onContinueInNl on GuidedWizardProps", () => {
    expect(guidedShellSrc).toContain("onContinueInNl?:");
  });

  it("forwards the prop to the inner AuthorWizard", () => {
    expect(guidedShellSrc).toMatch(
      /<AuthorWizard[\s\S]*?onContinueInNl=\{onContinueInNl\}/,
    );
  });
});


describe("CustomizeHub — wires the handoff into the NL surface", () => {
  it("extends the Add-policy phase with an optional nlPrefill seed (PR-4 shape)", () => {
    expect(hubSrc).toMatch(
      /phase: "policy"; surface: "describe" \| "linked"; nlPrefill\?: string/,
    );
  });

  it("flips to the describe surface with the primer when the wizard calls onContinueInNl", () => {
    expect(hubSrc).toMatch(
      /onContinueInNl=\{[\s\S]*?setAddState\(\{ phase: "policy", surface: "describe", nlPrefill: primer \}\)/,
    );
  });

  it("passes the seed through to NlRuleCompose via initialNlText", () => {
    expect(hubSrc).toMatch(/initialNlText=\{addState\.nlPrefill\}/);
  });
});


describe("NlRuleCompose — accepts the initialNlText seed", () => {
  it("declares initialNlText on NlRuleComposeProps", () => {
    expect(nlSrc).toContain("initialNlText?:");
  });

  it("seeds the local nlText state from initialNlText", () => {
    expect(nlSrc).toMatch(
      /useState<string>\(initialNlText \?\? ""\)/,
    );
  });
});
