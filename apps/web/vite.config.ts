import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  root: path.resolve(__dirname),
  base: "/app/",
  plugins: [react()],
  publicDir: "public",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    minify: false,
    rollupOptions: {
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith(".css")) return "styles.css";
          return "assets/[name]-[hash][extname]";
        },
      },
    },
  },
});
