import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = "src/chat-core";
const FORBIDDEN = /from\s+["'](react|react-dom|next\/|@privy-io\/|@supabase\/|@\/)/;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const violations = [];
for (const file of walk(ROOT)) {
  if (!file.endsWith(".ts")) continue;
  const src = readFileSync(file, "utf8");
  for (const line of src.split("\n")) {
    if (FORBIDDEN.test(line)) violations.push(`${file}: ${line.trim()}`);
  }
}

if (violations.length) {
  console.error("chat-core boundary violations (framework/app imports forbidden):");
  for (const v of violations) console.error("  " + v);
  process.exit(1);
}
console.log("chat-core boundary OK");
