import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./packs-dashboard-api.ts", import.meta.url), "utf8");

describe("packs-dashboard-api", () => {
  it("exposes getDashboardChecks / putDashboardCheck / deleteDashboardCheck / getDashboardPacksMenu", () => {
    expect(src).toContain("export async function getDashboardChecks");
    expect(src).toContain("export async function putDashboardCheck");
    expect(src).toContain("export async function deleteDashboardCheck");
    expect(src).toContain("export async function getDashboardPacksMenu");
  });

  it("uses /v1/app/packs/dashboard endpoints", () => {
    expect(src).toContain("/v1/app/packs/dashboard/checks");
    expect(src).toContain("/v1/app/packs/dashboard/menu");
  });
});
