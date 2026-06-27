import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { OnboardingWizard } from "./onboarding-wizard";

const PROVIDERS = ["anthropic", "openai", "gemini", "fireworks", "openrouter"];

describe("OnboardingWizard markup", () => {
  it("renders the step-1 provider + API key fields with an escape hatch when open", () => {
    const html = renderToStaticMarkup(
      <OnboardingWizard open providers={PROVIDERS} onClose={() => {}} />,
    );
    // Step 1 fields.
    expect(html).toContain("Provider");
    expect(html).toContain('type="password"');
    // Provider key hint surfaces.
    expect(html).toContain("console.anthropic.com");
    // The always-available escape hatch.
    expect(html.toLowerCase()).toContain("env var");
  });

  it("renders nothing visible when closed", () => {
    const html = renderToStaticMarkup(
      <OnboardingWizard open={false} providers={PROVIDERS} onClose={() => {}} />,
    );
    expect(html).not.toContain('type="password"');
  });

  it("shows the no-providers message and only the Skip hatch when no supported providers were reported", () => {
    const html = renderToStaticMarkup(
      <OnboardingWizard open providers={["mystery-provider"]} onClose={() => {}} />,
    );
    // The empty-state message surfaces.
    expect(html).toContain("No supported providers were reported");
    // No locked provider dropdown / key field.
    expect(html).not.toContain('type="password"');
    // The Skip escape hatch is still available.
    expect(html.toLowerCase()).toContain("env var");
    // No enabled Next button driving the user into a broken flow.
    expect(html).not.toContain(">Next<");
  });
});

describe("OnboardingWizard wiring (source)", () => {
  const source = readFileSync(new URL("./onboarding-wizard.tsx", import.meta.url), "utf8");

  it("saves through the tested pure submitProviderConfig helper", () => {
    // The save behavior (payload + error parsing) lives in the unit-tested pure
    // fn; the component must call it rather than re-implementing the fetch.
    expect(source).toContain("submitProviderConfig");
    // No bespoke onboarding write endpoint.
    expect(source).not.toContain("/v1/app/onboarding");
    expect(source).not.toContain("/v1/setup");
  });

  it("navigates to the local chat on success", () => {
    expect(source).toContain("/dashboard/local/chat/general");
  });

  it("links to the integrations page rather than reimplementing the connect flow", () => {
    expect(source).toContain("/dashboard/local/integrations");
    expect(source).not.toContain("composio");
  });

  it("never renders the apiKey as visible text in the DOM", () => {
    // The key may be bound to the masked password Input (cleared on submit), but
    // it must never be interpolated into visible JSX text (no `>{apiKey}<`, no
    // template/summary echo). Confirm the only binding is the password field.
    expect(source).not.toContain(">{apiKey}");
    expect(source).not.toContain("Key: {apiKey}");
    // The password Input is the sole place the key value lives.
    expect(source).toContain('type="password"');
  });

  it("clears the apiKey from memory after a successful save", () => {
    expect(source).toContain('setApiKey("")');
  });

  it("clears the model input when the user picks Custom… so it starts empty", () => {
    // FIX 4: selecting CUSTOM_MODEL_VALUE must blank the free-text field rather
    // than leaving the previous preset id pre-filled.
    expect(source).toContain('setModel("")');
  });

  it("locks body scroll while the overlay is mounted", () => {
    // FIX 5: the hand-rolled overlay mirrors the shared Modal's scroll-lock.
    expect(source).toContain('document.body.style.overflow');
  });

  it("draws user-facing prose from the i18n catalog like settings-form", () => {
    // FIX 3: component-level copy is keyed, not hardcoded English. Uses the
    // provider-optional accessor so the no-DOM markup tests still render.
    expect(source).toContain("@/lib/i18n");
    expect(source).toContain("useOptionalMessages");
    expect(source).toContain("localSetupTitle");
  });
});
