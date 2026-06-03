import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");

describe("README public OSS contract", () => {
  it("uses the public OpenMagi tagline instead of the weaker local-first line", () => {
    expect(readme).toContain(
      "The programmable agent that runs on rules you write",
    );
    expect(readme).not.toContain(
      "Local-first agent runtime and CLI: memory, tools, evidence gates, and simple setup.",
    );
  });

  it("keeps Homebrew as the primary install path with local dashboard commands", () => {
    const quickstartIndex = readme.indexOf("## Quickstart");
    const homebrewIndex = readme.indexOf("### Homebrew");
    const developerIndex = readme.indexOf("### From source");

    expect(quickstartIndex).toBeGreaterThanOrEqual(0);
    expect(homebrewIndex).toBeGreaterThan(quickstartIndex);
    expect(developerIndex).toBeGreaterThan(homebrewIndex);
    expect(readme).toContain("brew install openmagi/tap/magi-agent");
    expect(readme).toContain("magi-agent serve --port 8080");
    expect(readme).toContain("open http://localhost:8080/dashboard");
    expect(readme).toContain("magi --help");
    expect(readme).toContain("magi-agent --help");
  });

  it("does not describe hosted-internal rollout mechanisms in the public README", () => {
    const forbidden = [
      "selected-bot gates",
      "hosted runtime",
      "hosted runtimes",
      "production authority",
      "Supabase",
      "Cloud/Supabase/billing",
    ];

    for (const phrase of forbidden) {
      expect(readme).not.toContain(phrase);
    }
  });
});
