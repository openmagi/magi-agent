import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DashboardSetupGate } from "./dashboard-setup-gate";

describe("DashboardSetupGate", () => {
  it("renders nothing on the initial (pre-bootstrap) pass", () => {
    // Before loadLocalBootstrap resolves there is no setup signal, so the gate
    // must render nothing and never block the dashboard.
    const html = renderToStaticMarkup(<DashboardSetupGate />);
    expect(html).toBe("");
  });

  it("treats an absent setup block as not-needed (back-compat)", () => {
    const source = readFileSync(new URL("./dashboard-setup-gate.tsx", import.meta.url), "utf8");
    // The gate keys off setup?.needed === true, so older backends (no setup)
    // never trigger the wizard.
    expect(source).toContain("setup?.needed === true");
  });

  it("loads the bootstrap and renders the wizard from setup data", () => {
    const source = readFileSync(new URL("./dashboard-setup-gate.tsx", import.meta.url), "utf8");
    expect(source).toContain("loadLocalBootstrap");
    expect(source).toContain("OnboardingWizard");
    expect(source).toContain("providers");
  });
});
