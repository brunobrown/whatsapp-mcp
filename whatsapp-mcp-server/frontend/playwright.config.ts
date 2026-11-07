import { defineConfig } from "@playwright/test";

/**
 * One worker, one browser, no retries: a test that fails once is a test that fails. Each spec file starts its own
 * interface process (see e2e/support/fixtures.ts), so nothing is shared between files and no web server is
 * declared here. The pages must be built first (`npx vite build`), which the interface serves from ui/web.
 */
export default defineConfig({
  testDir: "e2e",
  retries: 0,
  workers: 1,
  fullyParallel: false,
  forbidOnly: true,
  reporter: "list",
  timeout: 30_000,
  expect: { timeout: 8000 },
  use: { browserName: "chromium", trace: "off", screenshot: "off", video: "off" },
});
