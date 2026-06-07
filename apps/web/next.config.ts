import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  distDir: "dist",
  // All pages are client-rendered — no SSR needed for local agent dashboard
  images: {
    unoptimized: true,
  },
  typescript: {
    // Cloud-only library code has type stubs; full type-check via `tsc --noEmit` separately
    ignoreBuildErrors: true,
  },
};

export default nextConfig;
