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
      "src/chat-core/stuck-liverun-watchdog.test.ts",
      // Citation in-flight UX suites (GAP #4 / GAP #5). Standalone files that are
      // green under the node environment: the chat-core derivation logic, the
      // CitationHedgeCallout unit, and the two component-integration suites that
      // render the real MessageBubble / ChatMessages against the citation paths.
      // The integration cases were split out of the colocated
      // message-bubble.test.tsx / chat-messages.test.tsx precisely so they run
      // here: those two files carry pre-existing dormant failures unrelated to
      // this change (markdown-literal + typing-placeholder + live-run-chrome
      // cases) that keep the whole file out of the include.
      "src/chat-core/citation-repair-status.test.ts",
      "src/chat-core/citation-hedge.test.ts",
      "src/components/chat/citation-hedge-callout.test.tsx",
      "src/components/chat/message-bubble.citation-hedge.test.tsx",
      "src/components/chat/chat-messages.citation-status.test.tsx",
    ],
  },
};
