import { defineConfig, devices } from "@playwright/test";

/** The stack (API + fake models + built dashboard) is started by e2e/global-setup.ts on this port. */
export const API_PORT = Number(process.env.E2E_API_PORT ?? 8765);

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  workers: 1,
  fullyParallel: false,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://127.0.0.1:${API_PORT}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
