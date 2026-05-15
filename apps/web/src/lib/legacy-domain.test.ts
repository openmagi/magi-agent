import { describe, expect, it } from "vitest";
import { buildOpenMagiRedirectUrl, isLegacyClawyHost } from "./legacy-domain";

describe("legacy Clawy domain redirect", () => {
  it("detects only the legacy Clawy production hosts", () => {
    expect(isLegacyClawyHost("clawy.pro")).toBe(true);
    expect(isLegacyClawyHost("www.clawy.pro")).toBe(true);
    expect(isLegacyClawyHost("CLAWY.PRO.")).toBe(true);
    expect(isLegacyClawyHost("openmagi.ai")).toBe(false);
    expect(isLegacyClawyHost("localhost")).toBe(false);
  });

  it("preserves the current path, query, and hash on openmagi.ai", () => {
    expect(
      buildOpenMagiRedirectUrl({
        hostname: "clawy.pro",
        pathname: "/dashboard/bot-123/chat",
        search: "?channel=telegram",
        hash: "#latest",
      }),
    ).toBe("https://openmagi.ai/dashboard/bot-123/chat?channel=telegram#latest");
  });
});
