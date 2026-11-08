/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Scaffold mirrors agentculture/embodiment web/vite.config.ts (cite, don't
// import). The API is nfc-cli's local server (`nfc-cli ui serve`, plan task
// t14): GET /api/state, POST /api/{noun}/{verb}, SSE /api/events. Same-origin
// in production (the server serves dist/); proxied in dev so the browser never
// needs CORS. Default port 8770 matches the server's default bind.
const API_TARGET = process.env.NFC_CLI_API ?? "http://127.0.0.1:8770";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    // dist/ must reference no external origin (scripts/check-no-external-origin.mjs);
    // keep sourcemaps out of the shipped bundle.
    sourcemap: false,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
