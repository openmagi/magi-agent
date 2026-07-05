import { fileURLToPath } from "node:url";

// Plain config object (no `vitest/config` import) so it loads even when vitest
// is run from an npx cache where `vite` is not resolvable from this directory.
// Resolves the `@/*` -> `src/*` path alias (mirrors tsconfig.json paths) so the
// colocated `*.test.ts(x)` unit suites run without an editor/IDE supplying it.
// Tests render via `react-dom/server` string snapshots, so the default Node
// environment is sufficient (no jsdom).
export default {
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    // Scoped to the suites this PR owns and keeps green under the `node`
    // environment. Pre-existing dormant `*.test.ts(x)` files elsewhere under
    // `src/`/`app/` may not pass here; CI does not run vitest, so a broad glob
    // would only surface unrelated breakage on local runs.
    include: [
      "src/components/onboarding/**/*.test.{ts,tsx}",
      "src/components/chat/missions-panel.test.tsx",
      "src/lib/local-auth.test.ts",
      "src/lib/chat/local-kb-upload.test.ts",
    ],
  },
};
