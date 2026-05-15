import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  distDir: "dist",
  // All pages are client-rendered — no SSR needed for local agent dashboard
  images: {
    unoptimized: true,
  },
  typescript: {
    // Type-check via `npm run lint` separately
    ignoreBuildErrors: false,
  },
};

export default nextConfig;
