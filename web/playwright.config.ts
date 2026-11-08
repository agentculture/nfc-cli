import { defineConfig, devices } from "@playwright/test";

// e2e runs against the built bundle served by `vite preview`; every /api/*
// request is answered by request interception (e2e/fixtures/api.ts), so no
// Python server and no reader hardware are involved. CI (ubuntu-latest) is
// the authoritative run; headless Chromium may not start on the aarch64 board
// without root-installed system libraries.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run build && npm run preview",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
