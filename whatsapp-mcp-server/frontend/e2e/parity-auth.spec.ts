import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, FIXTURE_DIR, harnessForFile, PASSWORD, request, test, throttlePause, withHarness } from "./support/fixtures";
import type { Harness } from "./support/fixtures";

// Parity with 0.6.0 for AUTH-01..AUTH-07 of the interface-grafica spec. One test per requirement, titled with its ID.

const harness = harnessForFile();

const NEW_PASSWORD = "a different long password";
const FAILED = "Sign-in failed. Check the details and try again.";
const WEAK = "The password must have at least 12 characters.";

interface AuthRecord {
  version: number;
  scrypt: { n: number; r: number; p: number };
  salt: string;
  hash: string;
}

function readAuth(from: Harness): AuthRecord {
  return JSON.parse(readFileSync(from.file("ui_auth.json"), "utf8")) as AuthRecord;
}

function mode(path: string): number {
  return statSync(path).mode & 0o777;
}

test("AUTH-01 without a session only the sign-in page shows and no configuration is loaded", async ({ page }) => {
  const { url } = harness.current;
  const asked: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) asked.push(`${request.method()} ${path}`);
  });
  await page.goto(`${url}/`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  // A section address does not get around it either.
  await page.goto(`${url}/s/send_policy`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await expect(page.getByTestId("shell")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Sections" })).toHaveCount(0);
  expect(new Set(asked)).toEqual(new Set(["GET /api/session"]));
  // The API itself refuses, and its body holds no configuration.
  const refused = await page.request.get(`${url}/api/sections/send_policy`);
  expect(refused.status()).toBe(401);
  expect(Object.keys((await refused.json()) as object).sort()).toEqual(["code", "message"]);
});

test("AUTH-02 with no password the page asks for the setup code that the log prints", async ({ page }) => {
  await withHarness({ password: false }, async (fresh) => {
    const code = await fresh.setupCode();
    await page.goto(`${fresh.url}/`);
    await expect(page.getByRole("heading", { level: 1, name: "Create the password" })).toBeVisible();
    await expect(page.getByLabel("Setup code")).toBeVisible();
    // Asking for a code prints a new one in the log and never shows it on the page.
    const before = fresh.output.length;
    await page.getByRole("button", { name: "Print a setup code" }).click();
    await expect(page.getByRole("status").filter({ hasText: "printed in the bridge log" }).last()).toBeVisible();
    const printed = await fresh.setupCode(before);
    expect(printed).toMatch(/^[A-Z0-9-]{8,}$/);
    await expect(page.locator("body")).not.toContainText(printed);
    await expect(page.locator("body")).not.toContainText(code);
    // A wrong code does not create the password; the right one does.
    await page.getByLabel("Setup code").fill("WRONGCODE123");
    await page.getByLabel("New password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Repeat the password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create password" }).click();
    await expect(page.getByRole("alert")).toContainText(FAILED);
    expect(fresh.read("ui_auth.json")).toBeUndefined();
    await throttlePause();
    await page.getByLabel("Setup code").fill(printed);
    await page.getByLabel("New password", { exact: true }).fill(PASSWORD);
    await page.getByLabel("Repeat the password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create password" }).click();
    await expect(page.getByTestId("shell")).toBeVisible();
    expect(fresh.read("ui_auth.json")).toBeDefined();
  });
});

test("AUTH-03 only the hash of the password is kept, in a 0600 file shaped as 0.6.0 wrote it", () => {
  const { configDir } = harness.current;
  const path = harness.current.file("ui_auth.json");
  expect(mode(path)).toBe(0o600);
  const written = readAuth(harness.current);
  const reference = JSON.parse(readFileSync(join(FIXTURE_DIR, "ui_auth.json"), "utf8")) as AuthRecord;
  expect(Object.keys(written).sort()).toEqual(Object.keys(reference).sort());
  expect(written.version).toBe(reference.version);
  expect(written.scrypt).toEqual(reference.scrypt);
  expect(written.salt).toMatch(/^[0-9a-f]{32}$/);
  expect(written.hash).toMatch(/^[0-9a-f]{64}$/);
  expect(written.salt).not.toBe(reference.salt);
  // The password is in no file of the configuration directory, in any encoding that is plain text.
  for (const name of readdirSync(configDir)) {
    const text = readFileSync(join(configDir, name), "latin1");
    expect(text).not.toContain(PASSWORD);
  }
});

test("AUTH-03 a ui_auth.json written by 0.6.0 is accepted: its password signs in", async ({ page }) => {
  await withHarness({ seed: ["ui_auth.json"] }, async (fresh) => {
    const before = readFileSync(fresh.file("ui_auth.json"), "utf8");
    await fresh.signIn(page);
    expect(readFileSync(fresh.file("ui_auth.json"), "utf8")).toBe(before);
  });
});

test("AUTH-04 a wrong password is refused with one generic message and repeated tries make the next wait grow", async ({ page }) => {
  const { url } = harness.current;
  await page.goto(`${url}/`);
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText(FAILED);
  // Two tries in a row: the second one is made to wait, without being checked.
  const api = page.request;
  const session = (await (await api.get(`${url}/api/session`)).json()) as { csrf: string };
  const headers = { "X-CSRF-Token": session.csrf };
  const waits: number[] = [];
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const answer = await api.post(`${url}/api/session/login`, { headers, data: { password: "still wrong" } });
    const body = (await answer.json()) as { ok: boolean; message?: string; wait?: number };
    if (answer.status() === 429) {
      waits.push(body.wait ?? 0);
      expect(Number(answer.headers()["retry-after"])).toBe(body.wait);
    } else {
      expect(body).toEqual({ ok: false, message: FAILED });
    }
  }
  expect(waits.length).toBeGreaterThan(0);
  expect(Math.min(...waits)).toBeGreaterThan(0);
  // The right password during the wait is refused too, and the page counts the wait down.
  await page.getByLabel("Password").fill(PASSWORD);
  const submit = page.getByRole("button", { name: /^(Sign in|Wait \d+ s)$/ });
  await submit.click();
  await expect(page.getByRole("alert")).toContainText("Too many attempts");
  await expect(page.getByRole("button", { name: /^Wait \d+ s$/ })).toBeDisabled();
  // Once the wait is over the right password works.
  await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled({ timeout: 90_000 });
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("shell")).toBeVisible();
});

test("AUTH-05 signing out ends the session and an idle session expires back to the sign-in page", async ({ page }) => {
  const { url } = harness.current;
  await harness.current.signIn(page);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  // The old cookie opens nothing.
  expect((await page.request.get(`${url}/api/sections`)).status()).toBe(401);
  await page.goto(`${url}/s/send_policy`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();

  await withHarness({ idleSeconds: 2 }, async (fresh) => {
    await fresh.signIn(page);
    // Wait until the server says the session lapsed. The look is made from a second client holding the same cookie,
    // so it neither renews the session (background read) nor replaces the cookie of the page.
    const cookie = (await page.context().cookies()).map((c) => `${c.name}=${c.value}`).join("; ");
    const watcher = await request.newContext({ extraHTTPHeaders: { Cookie: cookie, "X-Background": "1" } });
    await expect
      .poll(async () => ((await (await watcher.get(`${fresh.url}/api/session`)).json()) as { state: string }).state, {
        timeout: 20_000,
      })
      .toBe("login");
    await watcher.dispose();
    await page.getByRole("link", { name: /Monitor profiles/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
    await expect(page.getByText("Your session ended because of inactivity.").first()).toBeVisible();
  });
});

test("AUTH-06 a password under 12 characters is refused when it is created and when it is changed", async ({ page }) => {
  await withHarness({ password: false }, async (fresh) => {
    const short = "elevenchars";
    expect(short).toHaveLength(11);
    await page.goto(`${fresh.url}/`);
    await page.getByLabel("Setup code").fill(await fresh.setupCode());
    await page.getByLabel("New password", { exact: true }).fill(short);
    await page.getByLabel("Repeat the password").fill(short);
    await page.getByRole("button", { name: "Create password" }).click();
    await expect(page.getByRole("alert")).toHaveText(WEAK);
    expect(fresh.read("ui_auth.json")).toBeUndefined();
    // Twelve characters is enough (the code was not burnt by a weak password).
    await page.getByLabel("Setup code").fill(await fresh.setupCode());
    await page.getByLabel("New password", { exact: true }).fill("twelvechars!");
    await page.getByLabel("Repeat the password").fill("twelvechars!");
    await page.getByRole("button", { name: "Create password" }).click();
    await expect(page.getByTestId("shell")).toBeVisible();

    const created = readFileSync(fresh.file("ui_auth.json"), "utf8");
    await page.goto(`${fresh.url}/account`);
    await page.getByLabel("Current password").fill("twelvechars!");
    await page.getByLabel("New password", { exact: true }).fill(short);
    await page.getByLabel("Repeat the new password").fill(short);
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByRole("alert")).toHaveText(WEAK);
    expect(readFileSync(fresh.file("ui_auth.json"), "utf8")).toBe(created);
  });
});

test("AUTH-07 changing the password asks for the current one", async ({ page }) => {
  await withHarness({}, async (fresh) => {
    await fresh.signIn(page);
    await page.goto(`${fresh.url}/account`);
    const before = readFileSync(fresh.file("ui_auth.json"), "utf8");
    const fill = async (current: string): Promise<void> => {
      await page.getByLabel("Current password").fill(current);
      await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
      await page.getByLabel("Repeat the new password").fill(NEW_PASSWORD);
      await page.getByRole("button", { name: "Change password" }).click();
    };
    await fill("not the current one");
    await expect(page.getByRole("alert")).toHaveText(FAILED);
    expect(readFileSync(fresh.file("ui_auth.json"), "utf8")).toBe(before);
    await throttlePause();
    await fill(PASSWORD);
    await expect(page.getByRole("main").getByText("Password changed.")).toBeVisible();
    const after = readAuth(fresh);
    expect(after.hash).not.toBe((JSON.parse(before) as AuthRecord).hash);
    expect(statSync(fresh.file("ui_auth.json")).mode & 0o777).toBe(0o600);
    // Signing out and in again: only the new password works.
    await page.getByRole("button", { name: "Sign out of the interface" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("alert")).toHaveText(FAILED);
    await throttlePause();
    await page.getByLabel("Password").fill(NEW_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("shell")).toBeVisible();
  });
});

