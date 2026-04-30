import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const retiredProductName = ["open", "claw"].join("");
const retiredHiddenDir = [".", retiredProductName].join("");
const retiredHeader = ["x", retiredProductName, "session", "key"].join("-");
const retiredBinHelper = ["with", "Open", "claw", "Bin", "Path"].join("");
const retiredBinConstant = ["OPEN", "CLAW", "BIN"].join("_");

const bannedTerms = [
  retiredProductName,
  retiredHiddenDir,
  retiredHeader,
  retiredBinHelper,
  retiredBinConstant,
];

const binaryExtensions = new Set([
  ".ai",
  ".gif",
  ".ico",
  ".jpg",
  ".jpeg",
  ".pdf",
  ".png",
  ".webp",
]);

function trackedFiles(): string[] {
  return execFileSync("git", ["ls-files"], { encoding: "utf8" })
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((file) => !binaryExtensions.has(path.extname(file).toLowerCase()));
}

describe("public branding cleanup", () => {
  it("does not contain retired product identifiers in tracked text files or paths", () => {
    const violations: string[] = [];
    for (const file of trackedFiles()) {
      const lowerFile = file.toLowerCase();
      for (const term of bannedTerms) {
        if (lowerFile.includes(term.toLowerCase())) {
          violations.push(`${file}: path contains ${term}`);
        }
      }

      const content = readFileSync(file, "utf8");
      const lowerContent = content.toLowerCase();
      for (const term of bannedTerms) {
        if (lowerContent.includes(term.toLowerCase())) {
          violations.push(`${file}: content contains ${term}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
