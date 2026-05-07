import { spawnSync } from "node:child_process";

const required = [
  {
    command: "cargo",
    install: "Install Rust stable from https://rustup.rs/ and reopen your terminal.",
  },
  {
    command: "rustc",
    install: "Install Rust stable from https://rustup.rs/ and reopen your terminal.",
  },
];

let ok = true;

for (const item of required) {
  const result = spawnSync(item.command, ["--version"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

  if (result.status === 0) {
    const version = result.stdout.trim() || result.stderr.trim();
    console.log(`${item.command}: ${version}`);
    continue;
  }

  ok = false;
  console.error(`${item.command}: missing`);
  console.error(item.install);
}

if (!ok) {
  console.error("");
  console.error("Magi desktop uses Tauri v2, so Rust/Cargo and the platform WebView prerequisites are required.");
  process.exit(1);
}
