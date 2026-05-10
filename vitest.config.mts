import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "apps/web/src"),
      "next/link": path.resolve(__dirname, "apps/web/src/shims/next-link.tsx"),
      "next/navigation": path.resolve(__dirname, "apps/web/src/shims/next-navigation.ts"),
      "next/image": path.resolve(__dirname, "apps/web/src/shims/next-image.tsx"),
      "next/dynamic": path.resolve(__dirname, "apps/web/src/shims/next-dynamic.tsx"),
    },
  },
  test: {
    environment: "node",
  },
});
