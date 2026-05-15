import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("dashboard accessibility foundation", () => {
  it("does not disable user zoom in the Next viewport configuration", () => {
    const source = readFileSync(new URL("../../app/layout.tsx", import.meta.url), "utf8");
    const viewportBlock = source.match(/export const viewport: Viewport = \{[\s\S]*?\};/)?.[0] ?? "";

    expect(viewportBlock).toContain('width: "device-width"');
    expect(viewportBlock).toContain("initialScale: 1");
    expect(viewportBlock).not.toContain("maximumScale");
    expect(viewportBlock).not.toContain("userScalable");
  });

  it("keeps a visible global focus indicator without applying it to every focused element", () => {
    const source = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");
    const focusBlock = source.match(/:where\([\s\S]*?\):focus-visible \{[\s\S]*?\}/)?.[0] ?? "";
    const formControlBlock = source.match(/:where\(input, textarea, select\):focus-visible \{[\s\S]*?\}/)?.[0] ?? "";

    expect(source).not.toMatch(/\n:focus-visible \{/);
    expect(focusBlock).toContain("button");
    expect(focusBlock).toContain("outline: 2px solid");
    expect(focusBlock).toContain("outline-offset");
    expect(formControlBlock).toContain("outline: none");
    expect(formControlBlock).toContain("box-shadow: none");
  });

  it("renders shared buttons with an explicit focus-visible ring", () => {
    const source = readFileSync(new URL("./button.tsx", import.meta.url), "utf8");

    expect(source).toContain("focus-visible:ring-2");
    expect(source).toContain("focus-visible:ring-offset-2");
  });
});
