import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.BASE_URL || "http://localhost:8000";

/**
 * All tests share one app container (one SQLite DB, one $10k cash balance), so
 * we run serially (workers: 1). Specs that move cash read the value before and
 * compare relatively; the strict "fresh start" spec runs first as its own
 * project, and everything else depends on it.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 1,
  forbidOnly: !!process.env.CI,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    {
      name: "fresh-start",
      testMatch: /fresh-start\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
    {
      name: "api",
      testMatch: /api\.spec\.ts/,
      dependencies: ["fresh-start"],
    },
    {
      name: "chromium",
      testIgnore: [/fresh-start\.spec\.ts/, /api\.spec\.ts/],
      dependencies: ["fresh-start"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
  ],
});
