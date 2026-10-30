import { defineConfig } from "vite";

// Manifest V3 build: the side panel page (index.html) plus a separate background service worker
// emitted as dist/background.js. No inline scripts are produced (MV3 CSP).
export default defineConfig({
  base: "./",
  esbuild: { jsx: "automatic" },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
    modulePreload: false,
    rollupOptions: {
      input: { index: "index.html", background: "src/background.ts" },
      output: {
        entryFileNames: (chunk) => (chunk.name === "background" ? "background.js" : "assets/[name]-[hash].js"),
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
        manualChunks: { charts: ["recharts"] },
      },
    },
  },
});
