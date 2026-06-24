#!/usr/bin/env node
/* Design-system drift gate. Network-free, zero-dependency.
 *
 * Verifies the vendored design-system snapshot (components/ui/_ds) has not been
 * hand-edited since the last `sync-design-system.sh`. The expected hashes live
 * in _ds/MANIFEST.sha256, committed alongside the snapshot — so this check is
 * self-contained and never reaches out to the canonical source.
 *
 * Fails (exit 1) when:
 *   - a vendored file's sha256 differs from the manifest (local edit), or
 *   - a vendored file is missing or untracked (added/removed), or
 *   - a vendored source file lacks the GENERATED header.
 *
 * This file is itself vendored from magi-agent/scripts/check-ds-drift.mjs —
 * keep the three copies identical.
 */
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const CANDIDATES = [
  "components/ui/_ds",
  "src/components/ui/_ds",
  "apps/web/src/components/ui/_ds",
];

function findDsDir() {
  const fromArg = process.argv[2];
  if (fromArg) return resolve(fromArg);
  for (const c of CANDIDATES) {
    if (existsSync(c) && statSync(c).isDirectory()) return resolve(c);
  }
  return null;
}

const sha256 = (p) => createHash("sha256").update(readFileSync(p)).digest("hex");

function main() {
  const ds = findDsDir();
  if (!ds) {
    console.error("check-ds-drift: no _ds/ directory found. Run scripts/sync-design-system.sh from magi-agent.");
    process.exit(1);
  }
  const manifestPath = join(ds, "MANIFEST.sha256");
  if (!existsSync(manifestPath)) {
    console.error(`check-ds-drift: missing ${manifestPath}`);
    process.exit(1);
  }

  const expected = new Map();
  for (const line of readFileSync(manifestPath, "utf8").split("\n")) {
    const m = line.match(/^([0-9a-f]{64})\s+(.+)$/);
    if (m) expected.set(m[2], m[1]);
  }

  const errors = [];

  // present-on-disk set (everything except generated metadata)
  const META = new Set(["MANIFEST.sha256", ".ds-version"]);
  const onDisk = readdirSync(ds).filter((f) => !META.has(f));

  // 1. every manifest entry must exist + match
  for (const [rel, hash] of expected) {
    const fp = join(ds, rel);
    if (!existsSync(fp)) { errors.push(`missing vendored file: ${rel}`); continue; }
    if (sha256(fp) !== hash) errors.push(`hash mismatch (edited locally?): ${rel}`);
  }

  // 2. no untracked extras
  for (const f of onDisk) {
    if (!expected.has(f)) errors.push(`untracked file in _ds/: ${f}`);
  }

  // 3. GENERATED header on every vendored source/style file
  for (const f of onDisk) {
    if (!/\.(ts|tsx|css)$/.test(f)) continue;
    const head = readFileSync(join(ds, f), "utf8").slice(0, 200);
    if (!head.includes("GENERATED FILE — DO NOT EDIT")) {
      errors.push(`missing GENERATED header: ${f}`);
    }
  }

  if (errors.length) {
    console.error("check-ds-drift: FAILED\n  " + errors.join("\n  "));
    console.error("\nDesign-system files are vendored. Edit magi-agent/design-system and run scripts/sync-design-system.sh; do not edit _ds/ directly.");
    process.exit(1);
  }
  console.log(`check-ds-drift: OK (${expected.size} files, ${ds})`);
}

main();
