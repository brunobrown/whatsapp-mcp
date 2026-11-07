import { existsSync } from "node:fs";
import config from "../playwright.config";
import { expect, Harness, harnessForFile, test } from "./support/fixtures";

const harness = harnessForFile();

test("the harness serves the sign-in page at /", async ({ page }) => {
  await page.goto(`${harness.current.url}/`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("the harness signs in and shows the shell", async ({ page }) => {
  await harness.current.signIn(page);
  await expect(page.getByRole("navigation", { name: "Sections" })).toBeVisible();
});

test("the harness frees its port, its fake bridge and its directory when it stops", async () => {
  const own = new Harness({ password: false });
  await own.start();
  const { configDir, url, bridge } = own;
  const bridgeUrl = bridge.url;
  expect(existsSync(configDir)).toBe(true);
  expect((await fetch(`${url}/api/session`)).ok).toBe(true);
  await own.stop();
  expect(existsSync(configDir)).toBe(false);
  await expect(fetch(`${url}/api/session`)).rejects.toThrow();
  await expect(fetch(`${bridgeUrl}/status`)).rejects.toThrow();
});

test("the run is configured with no retries and one worker", () => {
  expect(config.retries).toBe(0);
  expect(config.workers).toBe(1);
  expect(config.forbidOnly).toBe(true);
});
