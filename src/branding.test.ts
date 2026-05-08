import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const legacyBrandParts = ["Cla", "wy"];
const legacyProjectParts = ["Open", "Cla", "w"];
const FORBIDDEN_BRAND_RE = new RegExp(
  `\\b(?:${legacyBrandParts.join("")}|${legacyBrandParts.join("").toLowerCase()}|${legacyBrandParts.join("").toUpperCase()}|${legacyProjectParts.join("")}|${legacyProjectParts.join("").toLowerCase()})\\b`,
);

function trackedTextFiles(): string[] {
  const out = execFileSync("git", ["ls-files"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  return out
    .split("\n")
    .filter((file) => file.length > 0)
    .filter((file) => fs.existsSync(path.join(REPO_ROOT, file)))
    .filter((file) => !file.endsWith(".png"))
    .filter((file) => !file.endsWith(".ico"))
    .filter((file) => !file.endsWith(".icns"));
}

describe("Magi rebrand", () => {
  it("does not leave legacy brand text in tracked text files", () => {
    const offenders: string[] = [];
    for (const file of trackedTextFiles()) {
      const absolute = path.join(REPO_ROOT, file);
      const text = fs.readFileSync(absolute, "utf8");
      if (FORBIDDEN_BRAND_RE.test(text) || FORBIDDEN_BRAND_RE.test(file)) {
        offenders.push(file);
      }
    }

    expect(offenders).toEqual([]);
  });
});
