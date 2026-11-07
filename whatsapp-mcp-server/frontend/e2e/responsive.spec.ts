import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, harnessForFile, PASSWORD, test } from "./support/fixtures";
import { json, savedNote } from "./support/sections";

const harness = harnessForFile({ seed: ["send_policy.json", "monitor_profiles.json", "webhooks.json", "channel.json", "ui_settings.json"] });

const PAGES = ["/status", "/activity", "/account", "/s/send_policy", "/s/monitor", "/s/webhooks", "/s/channel", "/s/agent_safety", "/s/database", "/s/media", "/s/privacy", "/s/timezone", "/s/interface"];

test("MIG-06 from 360 to 1280 px nothing scrolls sideways, and below 600 px one pane shows at a time with a way back", async ({ page }) => {
  const { url } = harness.current;
  harness.current.seedActivity({
    drafts: [{ id: "d_a_rather_long_identifier_for_a_draft", profile: "a-long-profile-name", chat_jid: "120363000000000001@g.us", sender: "15550100001", created_at: "2026-01-02T01:00:00+00:00", expires_at: "2099-01-01T00:00:00+00:00" }],
    send: [{ ts: "2026-01-02T01:00:00+00:00", kind: "text", chat_jid: "a-very-long-chat-identifier@s.whatsapp.net", chat_name: "A chat with a very long name indeed", size: 11, result: "sent", rule: "allow", override: false, message_id: "s1" }],
  });
  await harness.current.signIn(page);
  const nav = page.getByRole("navigation", { name: "Sections" });
  const main = page.getByRole("main");
  for (const width of [360, 599, 600, 768, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    for (const path of PAGES) {
      await page.goto(`${url}${path}`);
      await expect(page.getByTestId("shell")).toBeVisible();
      if (width >= 600) {
        // Two panes: the list on the left, the page on the right under its header.
        await expect(nav).toBeVisible();
        await expect(main).toBeVisible();
        await expect(page.getByRole("banner").getByRole("heading", { level: 1 })).toBeVisible();
        await expect(page.getByRole("button", { name: "Back to the sections" })).toHaveCount(0);
        const listBox = await nav.boundingBox();
        const mainBox = await main.boundingBox();
        expect(listBox !== null && mainBox !== null && listBox.x + listBox.width <= mainBox.x + 1, `${path} at ${String(width)}`).toBe(true);
      } else {
        // One pane: the list comes first; choosing an entry shows the page alone, and Back returns to the list.
        await expect(nav).toBeVisible();
        await expect(main).toHaveCount(0);
        await nav.locator(`a[href="${path}"]`).click();
        await expect(main).toBeVisible();
        await expect(nav).toHaveCount(0);
        await expect(page.getByRole("banner").getByRole("heading", { level: 1 })).toBeVisible();
        await page.getByRole("button", { name: "Back to the sections" }).click();
        await expect(nav).toBeVisible();
        await expect(main).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Back to the sections" })).toHaveCount(0);
        await nav.locator(`a[href="${path}"]`).click();
        await expect(main).toBeVisible();
      }
      const overflow = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth - window.innerWidth,
        body: document.body.scrollWidth - window.innerWidth,
      }));
      expect(overflow.page, `${path} at ${String(width)} px`).toBeLessThanOrEqual(0);
      expect(overflow.body, `${path} at ${String(width)} px`).toBeLessThanOrEqual(0);
    }
  }
});

/** What the focused element is, and whether the browser draws a ring on it. */
async function focused(page: Page): Promise<{ role: string; name: string; ring: boolean; visible: boolean }> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (el === null) return { role: "", name: "", ring: false, visible: false };
    const style = getComputedStyle(el);
    const name = el.getAttribute("aria-label") ?? (el as HTMLInputElement).labels?.[0]?.textContent ?? el.textContent;
    return {
      role: el.getAttribute("role") ?? el.tagName.toLowerCase(),
      name: name.trim(),
      ring: (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== "none",
      visible: el.matches(":focus-visible"),
    };
  });
}

/** Presses Tab until the focused control is the one `match` accepts; every stop on the way has a visible focus ring. */
async function tabTo(page: Page, role: string, name: string | RegExp): Promise<void> {
  for (let step = 0; step < 60; step += 1) {
    await page.keyboard.press("Tab");
    const here = await focused(page);
    if (here.role === "body") continue;
    expect(here.ring && here.visible, `focus is visible on ${here.role} "${here.name}"`).toBe(true);
    if (here.role === role && (typeof name === "string" ? here.name === name : name.test(here.name))) return;
  }
  throw new Error(`Tab never reached ${role} ${String(name)}`);
}

test("MIG-07 a full edit and save is done by keyboard alone, every stop with a visible focus", async ({ page }) => {
  const { url } = harness.current;
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${url}/`);
  await expect(page.getByLabel("Password")).toBeVisible();
  // Sign in: Tab to the field, type, Enter.
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.keyboard.type(PASSWORD);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("shell")).toBeVisible();
  // The first stop is the skip link; Enter moves to the content.
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to the content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#content")).toBeFocused();
  // The section loads after the shell: a Tab pressed before its fields exist would leave the page.
  await expect(page.getByRole("radio", { name: "allowlist" })).toBeVisible();
  // Mode: the arrow keys choose inside the group.
  await page.keyboard.press("Tab");
  const mode = await focused(page);
  expect(mode.role).toBe("radio");
  expect(mode.ring && mode.visible).toBe(true);
  // The saved file says allowlist; Up moves the choice to denylist (a radio group wraps around).
  await expect(page.getByRole("radio", { name: "allowlist" })).toBeChecked();
  await page.keyboard.press("ArrowUp", { delay: 60 });
  await expect(page.getByRole("radio", { name: "denylist" })).toBeChecked();
  // Add an item to Allow: Tab to the button inside the Allow group, Enter, type in the new field.
  await tabTo(page, "button", "Add item");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("textbox", { name: "Allow, item 3" })).toBeFocused();
  await page.keyboard.type("Keyboard Group");
  // Tab to Save (enabled now) and press Enter.
  await tabTo(page, "button", "Save");
  await page.keyboard.press("Enter");
  await expect(savedNote(page)).toBeVisible();
  const saved = json(harness.current, "send_policy.json") as { mode: string; allow: string[] };
  expect(saved.mode).toBe("denylist");
  expect(saved.allow).toEqual(["Example Group", "15550100001", "Keyboard Group"]);
});

test("MIG-07 controls expose a role and a name in the accessibility tree on every page", async ({ page }) => {
  await harness.current.signIn(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  const named = /^\s*- (button|textbox|switch|radio|combobox|checkbox|link|radiogroup|group|region|table)\b( "[^"]+")?/;
  for (const path of PAGES) {
    await page.goto(`${harness.current.url}${path}`);
    await expect(page.getByRole("main")).toBeVisible();
    const tree = await page.locator("body").ariaSnapshot();
    const unnamed = tree
      .split("\n")
      .map((line) => named.exec(line))
      .filter((found): found is RegExpExecArray => found !== null && found[2] === undefined)
      .map((found) => found.input.trim());
    expect(unnamed, `${path}: every control and group has a name`).toEqual([]);
    const result = await new AxeBuilder({ page })
      .withRules(["button-name", "label", "link-name", "aria-input-field-name", "aria-toggle-field-name", "aria-required-attr", "aria-valid-attr", "aria-valid-attr-value", "aria-allowed-attr", "aria-allowed-role", "scrollable-region-focusable", "landmark-one-main", "page-has-heading-one", "region"])
      .analyze();
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`), path).toEqual([]);
  }
});
