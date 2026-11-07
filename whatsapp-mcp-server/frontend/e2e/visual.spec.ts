import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, harnessForFile, test, withHarness } from "./support/fixtures";
import type { Harness } from "./support/fixtures";
import { confirm, openSection } from "./support/sections";

// MIG-16, MIG-17 and MIG-18: the look of the interface. Colors ease over 150 ms when a theme changes, so every check
// reads them after the transitions are switched off.

const harness = harnessForFile({ seed: ["send_policy.json", "monitor_profiles.json", "webhooks.json", "channel.json", "ui_settings.json"] });

const PAGES = ["/status", "/activity", "/account", "/s/send_policy", "/s/monitor", "/s/webhooks", "/s/channel", "/s/agent_safety", "/s/database", "/s/media", "/s/privacy", "/s/timezone", "/s/interface", "/no-such-page"];

const NO_MOTION = "*, *::before, *::after { transition: none !important; animation: none !important; }";

/** The computed color of an element's property as numbers. */
async function rgb(page: Page, selector: string, property: "backgroundColor" | "color"): Promise<[number, number, number]> {
  const value = await page.locator(selector).first().evaluate((el, name) => getComputedStyle(el)[name as "color"], property);
  const parts = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(value);
  if (parts === null) throw new Error(`not a color: ${value}`);
  return [Number(parts[1]), Number(parts[2]), Number(parts[3])];
}

function hue([r, g, b]: [number, number, number]): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === min) return 0;
  const d = max - min;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

test("MIG-16 from 600 px the sections are a chat-style list on the left and the open section sits on the right under its own header", async ({ page }) => {
  await harness.current.signIn(page);
  for (const width of [600, 768, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`${harness.current.url}/s/monitor`);
    const nav = page.getByRole("navigation", { name: "Sections" });
    const main = page.getByRole("main");
    await expect(main).toBeVisible();
    const left = await page.getByRole("complementary", { name: "Settings menu" }).boundingBox();
    const right = await main.boundingBox();
    const header = await page.getByRole("banner").boundingBox();
    expect(left !== null && right !== null && header !== null).toBe(true);
    if (left === null || right === null || header === null) return;
    // Side by side, the list narrower than the page, the page's header on top of it.
    expect(left.x + left.width, `${String(width)}: list is left of the page`).toBeLessThanOrEqual(right.x + 1);
    expect(left.width).toBeLessThan(right.width + 1);
    expect(left.width).toBeLessThanOrEqual(420);
    expect(header.y + header.height).toBeLessThanOrEqual(right.y + 1);
    expect(header.x).toBeGreaterThanOrEqual(left.x + left.width - 1);
    await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Monitor profiles" })).toBeVisible();
    // A row of the list: a round icon, a title and a line of description; the open one is marked and shaded.
    const open = nav.getByRole("link", { name: /^Monitor profiles/ });
    await expect(open).toHaveAttribute("aria-current", "page");
    await expect(open.locator("svg")).toHaveCount(1);
    await expect(open.locator("span.rounded-full")).toHaveCount(1);
    await expect(open.locator("span.truncate").first()).toHaveText("Monitor profiles");
    const other = nav.getByRole("link", { name: /^Privacy/ });
    await expect(other).not.toHaveAttribute("aria-current", "page");
    const shaded = await open.evaluate((el) => getComputedStyle(el).backgroundColor);
    const plain = await other.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(shaded).not.toBe(plain);
    // Choosing another row changes the right pane and its header, and the list stays.
    await other.click();
    await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Privacy" })).toBeVisible();
    await expect(nav).toBeVisible();
  }
});

test("MIG-17 the accent is the green of the #00a884 family, both themes exist and follow the system, and a forced choice survives a reload", async ({ page }) => {
  const { url } = harness.current;
  const requested: string[] = [];
  page.on("request", (request) => requested.push(request.url()));
  await page.goto(`${url}/login`);
  await page.addStyleTag({ content: NO_MOTION });

  // The system decides while nothing is forced, and follows it live.
  await page.emulateMedia({ colorScheme: "light" });
  const lightSurface = await rgb(page, "body", "backgroundColor");
  expect(lightSurface).toEqual([240, 242, 245]);
  await page.emulateMedia({ colorScheme: "dark" });
  const darkSurface = await rgb(page, "body", "backgroundColor");
  expect(darkSurface).toEqual([17, 27, 33]);
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);

  // The accent: the sign-in button and the green band are in the green family (hue 150 to 180) in both themes.
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    const button = await rgb(page, 'button[type="submit"]', "backgroundColor");
    expect(hue(button), `${scheme} accent ${button.join(",")}`).toBeGreaterThanOrEqual(150);
    expect(hue(button)).toBeLessThanOrEqual(180);
    const band = await rgb(page, "div.bg-accent-solid", "backgroundColor");
    expect(hue(band)).toBeGreaterThanOrEqual(150);
    expect(hue(band)).toBeLessThanOrEqual(180);
  }
  expect(await rgb(page, 'button[type="submit"]', "backgroundColor")).toEqual([0, 168, 132]);

  // Signed in: the theme button cycles System, Light, Dark, and the choice beats the system.
  await harness.current.signIn(page);
  await page.addStyleTag({ content: NO_MOTION });
  await page.emulateMedia({ colorScheme: "dark" });
  const cycler = (name: string) => page.getByRole("button", { name: `Theme: ${name}. Change it.` });
  await cycler("System").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await rgb(page, "body", "backgroundColor")).toEqual([240, 242, 245]);
  // The choice is kept in this browser: a reload, and a new tab of the same browser, still show light.
  await page.reload();
  await page.addStyleTag({ content: NO_MOTION });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(cycler("Light")).toBeVisible();
  expect(await rgb(page, "body", "backgroundColor")).toEqual([240, 242, 245]);
  const second = await page.context().newPage();
  await second.goto(`${url}/status`);
  await expect(second.locator("html")).toHaveAttribute("data-theme", "light");
  await second.close();
  // Dark, kept the same way, and back to the system's choice.
  await cycler("Light").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await page.reload();
  await page.addStyleTag({ content: NO_MOTION });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await rgb(page, "body", "backgroundColor")).toEqual([17, 27, 33]);
  await cycler("Dark").click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
  await page.reload();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);

  // No logo, mark or icon of the brand is fetched or drawn: only the interface's own files.
  const own = new URL(url).origin;
  expect(requested.filter((address) => !address.startsWith(own))).toEqual([]);
  expect(requested.filter((address) => /whatsapp/i.test(new URL(address).pathname) || /\.(png|jpe?g|gif|ico|webp)(\?|$)/.test(address))).toEqual([]);
  await expect(page.locator("img")).toHaveCount(0);
});

interface Finding {
  where: string;
  rule: string;
  impact: string;
  nodes: string;
}

/** Axe on the page as it is, with the colors settled: every violation of any rule that is serious or critical, and any contrast one. */
async function scan(page: Page, where: string): Promise<Finding[]> {
  await page.addStyleTag({ content: NO_MOTION });
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return result.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical" || violation.id === "color-contrast")
    .map((violation) => ({
      where,
      rule: violation.id,
      impact: violation.impact ?? "",
      nodes: violation.nodes.map((node) => node.target.join(" ")).join(" | "),
    }));
}

async function scanBoth(page: Page, where: string): Promise<Finding[]> {
  const found: Finding[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    found.push(...(await scan(page, `${where} [${scheme}]`)));
  }
  return found;
}

test("MIG-18 axe finds no serious violation and no contrast failure in either theme on every page and in the states that add colors", async ({ page }) => {
  const found: Finding[] = [];
  harness.current.seedActivity({
    drafts: [{ id: "d_1", profile: "assistant", chat_jid: "120363000000000001@g.us", sender: "15550100001", created_at: "2026-01-02T01:00:00+00:00", expires_at: "2099-01-01T00:00:00+00:00" }],
    send: [{ ts: "2026-01-02T01:00:00+00:00", kind: "text", chat_jid: "a@s.whatsapp.net", chat_name: "A chat", size: 11, result: "blocked", rule: "deny", override: true, message_id: "s1" }],
    monitor: [{ ts: "2026-01-02T02:00:00+00:00", profile: "assistant", chat_jid: "c@g.us", sender: "x", message_id: "m1", triggers: ["mention"], mode: "draft", approval_used: false, result: "drafted", size: 5 }],
  });

  // Before sign-in: the sign-in page, and the first-run page of another interface.
  await page.goto(`${harness.current.url}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  found.push(...(await scanBoth(page, "/login")));
  await page.getByLabel("Password").fill("wrong password here");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  found.push(...(await scanBoth(page, "/login with a refusal")));
  await withHarness({ password: false }, async (first: Harness) => {
    await page.goto(`${first.url}/login`);
    await expect(page.getByRole("heading", { level: 1, name: "Create the password" })).toBeVisible();
    await page.getByRole("button", { name: "Print a setup code" }).click();
    await expect(page.getByRole("status").filter({ hasText: "printed in the bridge log" }).last()).toBeVisible();
    found.push(...(await scanBoth(page, "first run")));
  });

  // Every page of the shell.
  await harness.current.signIn(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  for (const path of PAGES) {
    await page.goto(`${harness.current.url}${path}`);
    await expect(page.getByTestId("shell").or(page.getByRole("heading", { level: 1, name: "Page not found" }))).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    found.push(...(await scanBoth(page, path)));
  }
  // The narrow layout, one pane.
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto(`${harness.current.url}/s/send_policy`);
  found.push(...(await scanBoth(page, "360 px list")));
  await page.getByRole("navigation", { name: "Sections" }).locator('a[href="/s/send_policy"]').click();
  found.push(...(await scanBoth(page, "360 px page")));
  await page.setViewportSize({ width: 1280, height: 900 });

  // States that bring colors of their own: a refusal, a warning, a notification, a dialog, a forced theme.
  await openSection(page, harness.current, "privacy");
  await page.getByRole("switch", { name: "Write message text in the log" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  found.push(...(await scanBoth(page, "privacy risk dialog")));
  await confirm(page, "Use this value");
  found.push(...(await scanBoth(page, "privacy with its warning")));
  await openSection(page, harness.current, "channel");
  await page.getByRole("textbox", { name: "Excerpt length" }).fill("not a number");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "needs attention" })).toBeVisible();
  found.push(...(await scanBoth(page, "channel with an error summary")));
  await openSection(page, harness.current, "send_policy");
  await page.getByRole("button", { name: "Remove item 1 of Allow" }).click();
  found.push(...(await scanBoth(page, "remove dialog")));
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Restore the default of Mode" }).waitFor();
  await page.getByRole("button", { name: /^Theme: / }).click();
  found.push(...(await scanBoth(page, "forced light")));
  await page.getByRole("button", { name: /^Theme: / }).click();
  found.push(...(await scanBoth(page, "forced dark")));

  expect(found.map((finding) => `${finding.where}: ${finding.rule} (${finding.impact}): ${finding.nodes}`)).toEqual([]);
});
