import { defineConfig } from "vite";

// Plain web build served by the SolHunter API at "/". In `vite dev`, API calls can be proxied to a local
// backend with VITE_API_BASE (e.g. VITE_API_BASE=http://localhost:8080 npm run dev).
export default defineConfig(() => {
  const target = process.env.VITE_API_BASE;
  return {
    base: "/",
    esbuild: { jsx: "automatic" },
    server: target
      ? { proxy: { "/api": { target, changeOrigin: true }, "/ws": { target, ws: true, changeOrigin: true }, "/health": target, "/ready": target } }
      : undefined,
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: false,
      target: "es2022",
      rollupOptions: {
        output: {
          entryFileNames: "assets/[name]-[hash].js",
          chunkFileNames: "assets/[name]-[hash].js",
          assetFileNames: "assets/[name]-[hash][extname]",
          manualChunks: { charts: ["recharts"] },
        },
      },
    },
  };
});
