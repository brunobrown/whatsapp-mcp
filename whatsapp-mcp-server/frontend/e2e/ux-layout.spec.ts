import AxeBuilder from "@axe-core/playwright";
import type { Locator, Page } from "@playwright/test";
import { expect, test, withHarness } from "./support/fixtures";
import { confirm, field, json, openSection, saveButton } from "./support/sections";
import { documentScrolls, panel, phoneOf, PHONE, shell, smallTargets, uxHarness } from "./support/ux";

// UXP-14 to 43 of the ui-ux-polish spec (UXP-15 also has the Python test of the section schema): cards, the
// contextual save bar, the live pages, the small touches and the phone rules. One interface per file, with extension
// sections, sixteen profiles and 45 log rows; a test that saves or needs another start uses a harness of its own.
//
// What Chromium cannot prove: the real on-screen keyboard and the real notch or home indicator. The keyboard is
// emulated with a stand-in `visualViewport` (the bar's own fallback offset) and with a shorter window (the
// interactive-widget=resizes-content model); the safe areas are emulated by setting the --safe-* variables that
// `env(safe-area-inset-*)` feeds. Whether iOS Safari and Android Chrome report those values is the platform's part.

const harness = uxHarness();

const FORMS = ["send_policy", "monitor", "webhooks", "channel", "agent_safety", "database", "media", "privacy", "timezone", "interface"];
const PAGES = ["/status", "/activity", "/account", ...FORMS.map((id) => `/s/${id}`), "/s/e2e_extra"];

/** The cards of the open page: its named `section` elements in the content, not the record cards nested inside one. */
const cards = (page: Page): Locator => page.locator("#content section[aria-labelledby]:not(section section)");

/** A field of the card itself, not a sub-field of a record inside it. */
const FIELD_OF_CARD = "[data-field]:not([data-field] [data-field])";

/** The headings of the cards, in order. */
async function cardTitles(page: Page): Promise<string[]> {
  return cards(page).evaluateAll((nodes) =>
    nodes.map((node) => document.getElementById(node.getAttribute("aria-labelledby") ?? "")?.firstChild?.textContent?.trim() ?? ""),
  );
}

const SURFACE = async (page: Page): Promise<string> => shell(page).evaluate((node) => getComputedStyle(node).backgroundColor);

test("UXP-14 every section form is cards with a heading, a border, a rounded corner and a surface color different from the page, in both themes", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    for (const id of FORMS) {
      await openSection(page, harness.current, id);
      const count = await cards(page).count();
      expect(count, `${id} has cards`).toBeGreaterThan(0);
      const page0 = await SURFACE(page);
      const looks = await cards(page).evaluateAll((nodes) =>
        nodes.map((node) => {
          const css = getComputedStyle(node);
          const heading = document.getElementById(node.getAttribute("aria-labelledby") ?? "");
          return {
            heading: heading?.textContent.trim() ?? "",
            level: heading?.tagName ?? "",
            border: parseFloat(css.borderTopWidth),
            radius: parseFloat(css.borderTopLeftRadius),
            surface: css.backgroundColor,
          };
        }),
      );
      for (const look of looks) {
        const where = `${id} / ${look.heading} / ${scheme}`;
        expect(look.heading, where).not.toBe("");
        expect(look.level, where).toBe("H2");
        expect(look.border, where).toBeGreaterThan(0);
        expect(look.radius, where).toBeGreaterThan(4);
        expect(look.surface, where).not.toBe(page0);
      }
    }
  }
  // One card per field group: the labelled fields share the card of their label, and a list stands alone.
  await openSection(page, harness.current, "channel");
  expect(await cardTitles(page)).toEqual(["Notices", "Monitor profiles", "Polls", "Excerpt", "Timing"]);
  await expect(page.getByRole("region", { name: "Timing", exact: true }).locator(FIELD_OF_CARD)).toHaveCount(4);
  await expect(page.getByRole("region", { name: "Excerpt", exact: true }).locator(FIELD_OF_CARD)).toHaveCount(2);
});

test("UXP-15 a field with a group label sits in the card headed by that label, and a field without one in the first card, General", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  let grouped = 0;
  let ungrouped = 0;
  // send_policy, channel and database label every field; media and webhooks label none.
  for (const id of ["send_policy", "channel", "database", "media", "webhooks"]) {
    const answer = await page.request.get(`${harness.current.url}/api/sections/${id}`);
    expect(answer.ok(), id).toBe(true);
    const view = (await answer.json()) as { fields: { key: string; kind: string; group?: string }[] };
    await openSection(page, harness.current, id);
    const titles = await cardTitles(page);
    if (view.fields.every((item) => (item.group?.trim() ?? "") !== "")) expect(titles, `${id}: no unlabelled field, no General card`).not.toContain("General");
    for (const item of view.fields) {
      // A records or list field is its own card, titled by its label (UXP-16): not placed by a group.
      if (item.kind === "records" || item.kind === "list") continue;
      const owner = await field(page, item.key).evaluate((node) => {
        const card = node.closest("section[aria-labelledby]");
        return document.getElementById(card?.getAttribute("aria-labelledby") ?? "")?.firstChild?.textContent?.trim() ?? "";
      });
      const label = item.group?.trim() ?? "";
      if (label === "") {
        ungrouped += 1;
        expect(owner, `${id}.${item.key} has no group`).toBe("General");
        expect(titles[0], `${id}: General is the first card`).toBe("General");
      } else {
        grouped += 1;
        expect(owner, `${id}.${item.key} is grouped`).toBe(label);
      }
    }
  }
  // The proof is not empty: grouped and ungrouped fields were both placed.
  expect(grouped).toBeGreaterThan(0);
  expect(ungrouped).toBeGreaterThan(0);
});

test("UXP-16 a records or list field with its own title is its own card", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  // Records: the profiles are one card holding the field and nothing else.
  await openSection(page, harness.current, "monitor");
  const profiles = page.getByRole("region", { name: "Profiles", exact: true });
  await expect(profiles).toHaveCount(1);
  await expect(profiles.locator(FIELD_OF_CARD)).toHaveCount(1);
  await expect(profiles.getByRole("heading", { level: 2, name: "Profiles" })).toBeVisible();
  await expect(profiles.getByRole("region", { name: "Profiles, items" })).toBeVisible();
  // Lists: Allow and Deny each stand alone, apart from the card that holds the mode.
  await openSection(page, harness.current, "send_policy");
  for (const label of ["Allow", "Deny"]) {
    const card = page.getByRole("region", { name: label, exact: true });
    await expect(card, label).toHaveCount(1);
    await expect(card.locator(FIELD_OF_CARD)).toHaveCount(1);
    await expect(card.getByRole("group")).toBeVisible();
    await expect(card.getByRole("radio")).toHaveCount(0);
  }
  const rules = page.getByRole("region", { name: "Rules", exact: true });
  await expect(rules.getByRole("radio", { name: "allowlist" })).toBeVisible();
  await expect(rules.getByRole("textbox", { name: "Allow, item 1" })).toHaveCount(0);
  // The order is the field order: the mode card, then Allow, then Deny.
  const titles = await cardTitles(page);
  expect(titles.indexOf("Rules")).toBeLessThan(titles.indexOf("Allow"));
  expect(titles.indexOf("Allow")).toBeLessThan(titles.indexOf("Deny"));
});

test("UXP-17 Status is the cards Bridge connection, Webhooks and Counters, and Drafts and audit the cards Pending drafts, Send audit and Monitor audit", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await page.goto(`${harness.current.url}/status`);
  await expect(page.getByRole("region", { name: "Bridge connection" })).toBeVisible();
  expect(await cardTitles(page)).toEqual(["Bridge connection", "Webhooks", "Counters"]);
  await page.goto(`${harness.current.url}/activity`);
  await expect(page.getByRole("region", { name: "Pending drafts", exact: true })).toBeVisible();
  expect(await cardTitles(page)).toEqual(["Pending drafts", "Send audit", "Monitor audit"]);
  // They are cards in the same sense as the forms': a border, a corner and their own surface.
  const looks = await cards(page).evaluateAll((nodes) => nodes.map((node) => ({ border: parseFloat(getComputedStyle(node).borderTopWidth), radius: parseFloat(getComputedStyle(node).borderTopLeftRadius), surface: getComputedStyle(node).backgroundColor })));
  const surface = await SURFACE(page);
  for (const look of looks) {
    expect(look.border).toBeGreaterThan(0);
    expect(look.radius).toBeGreaterThan(4);
    expect(look.surface).not.toBe(surface);
  }
});

test("UXP-18 from 1100 px the Status cards are in two columns and the form cards in one column of at most 48 rem, below 1100 px everything is one column", async ({ page }) => {
  await harness.current.signIn(page);
  const rem = 15;
  for (const [width, columns] of [[1280, 2], [1100, 2], [1099, 1], [1024, 1], [800, 1]] as const) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`${harness.current.url}/status`);
    const connection = page.getByRole("region", { name: "Bridge connection" });
    await expect(connection).toBeVisible();
    const one = await connection.boundingBox();
    const two = await page.getByRole("region", { name: "Webhooks", exact: true }).boundingBox();
    const three = await page.getByRole("region", { name: "Counters", exact: true }).boundingBox();
    if (columns === 2) {
      expect(two?.x, `${String(width)} px`).toBeGreaterThan((one?.x ?? 0) + (one?.width ?? 0) - 1);
      expect(two?.y).toBeCloseTo(one?.y ?? -1, 0);
      // Counters spans both columns, below them.
      expect(three?.x).toBeCloseTo(one?.x ?? -1, 0);
      expect((three?.x ?? 0) + (three?.width ?? 0)).toBeCloseTo((two?.x ?? 0) + (two?.width ?? 0), 0);
      expect(three?.y).toBeGreaterThan(Math.max((one?.y ?? 0) + (one?.height ?? 0), (two?.y ?? 0) + (two?.height ?? 0)) - 1);
    } else {
      expect(two?.x, `${String(width)} px`).toBeCloseTo(one?.x ?? -1, 0);
      expect(two?.y).toBeGreaterThan((one?.y ?? 0) + (one?.height ?? 0) - 1);
      expect(three?.y).toBeGreaterThan((two?.y ?? 0) + (two?.height ?? 0) - 1);
    }
    // Forms: one column, never wider than 48 rem.
    await openSection(page, harness.current, "channel");
    const formCards = await cards(page).evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect()).map((box) => ({ x: box.x, y: box.y, w: box.width })));
    expect(formCards.length).toBeGreaterThan(2);
    for (const card of formCards) {
      expect(card.x, `${String(width)} px`).toBeCloseTo(formCards[0]?.x ?? -1, 0);
      expect(card.w).toBeLessThanOrEqual(48 * rem + 1);
    }
    for (let index = 1; index < formCards.length; index += 1) expect(formCards[index]?.y).toBeGreaterThan(formCards[index - 1]?.y ?? 0);
  }
});

test("UXP-19 a card holding a field with an error is marked with an error icon and the words needs attention, and only that card", async ({ page }) => {
  await withHarness({ seed: ["channel.json"] }, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await fresh.signIn(page);
    await openSection(page, fresh, "channel");
    const marks = page.locator("#content section [data-needs-attention]");
    await expect(marks).toHaveCount(0);
    await field(page, "excerpt_chars").getByRole("textbox").fill("not a number");
    await saveButton(page).click();
    await expect(marks).toHaveCount(1);
    const mark = marks.first();
    await expect(mark).toHaveText("needs attention");
    await expect(mark.locator("svg")).toHaveCount(1);
    // It sits in the heading of the card that holds the field, and in no other card.
    const holder = page.getByRole("region", { name: /^Excerpt/ });
    await expect(holder.getByRole("heading", { level: 2 }).locator("[data-needs-attention]")).toHaveCount(1);
    await expect(holder.getByRole("textbox", { name: "Excerpt length" })).toHaveAttribute("aria-invalid", "true");
    for (const other of ["Notices", "Timing"]) {
      await expect(page.getByRole("region", { name: new RegExp(`^${other}`) }).locator("[data-needs-attention]")).toHaveCount(0);
    }
    // Once the value is right and saved the mark goes.
    await field(page, "excerpt_chars").getByRole("textbox").fill("100");
    await saveButton(page).click();
    await expect(saveButton(page)).toHaveCount(0);
    await expect(marks).toHaveCount(0);
  });
});

test("UXP-20 a form with no unsaved edits shows no save bar, and one edited back to what was saved shows none either", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await harness.current.signIn(page);
  await openSection(page, harness.current, "send_policy");
  await expect(page.locator("[data-save-bar]")).toHaveCount(0);
  await expect(saveButton(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Discard changes" })).toHaveCount(0);
  await expect(page.getByText("Unsaved changes")).toHaveCount(0);
  const allow = page.getByRole("textbox", { name: "Allow, item 1" });
  const saved = await allow.inputValue();
  await allow.fill(`${saved} edited`);
  await expect(page.locator("[data-save-bar]")).toBeVisible();
  await allow.fill(saved);
  await expect(page.locator("[data-save-bar]")).toHaveCount(0);
  // A read-only section never has one: the Database section only shows values.
  await openSection(page, harness.current, "database");
  await expect(page.locator("[data-save-bar]")).toHaveCount(0);
});

test("UXP-21 the first unsaved edit brings a bar with Unsaved changes, Discard changes and Save, announced politely, and Discard takes the edits and the bar away", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await harness.current.signIn(page);
  await openSection(page, harness.current, "send_policy");
  await expect(page.locator("[data-save-bar]")).toHaveCount(0);
  const allow = page.getByRole("textbox", { name: "Allow, item 1" });
  const saved = await allow.inputValue();
  await allow.fill(`${saved}!`);
  const bar = page.locator("[data-save-bar]");
  await expect(bar).toBeVisible();
  const note = bar.getByRole("status");
  await expect(note).toHaveText("Unsaved changes");
  // role=status is a polite live region; nothing on the bar makes it assertive.
  expect(await note.evaluate((node) => ({ live: node.getAttribute("aria-live"), role: node.getAttribute("role") }))).toEqual({ live: null, role: "status" });
  await expect(bar.locator("[aria-live=assertive], [role=alert]")).toHaveCount(0);
  await expect(bar.getByRole("button", { name: "Discard changes" })).toBeVisible();
  await expect(bar.getByRole("button", { name: "Save", exact: true })).toBeVisible();
  await bar.getByRole("button", { name: "Discard changes" }).click();
  await expect(bar).toHaveCount(0);
  await expect(allow).toHaveValue(saved);
});

test("UXP-22 Ctrl+S and Cmd+S save a form with edits when the focus is inside the section and stop the browser's save dialog, and do nothing outside it", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json"] }, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await fresh.signIn(page);
    await openSection(page, fresh, "send_policy");
    // Record what the browser would see after the page's handlers ran: a window listener runs last.
    await page.evaluate(() => {
      const seen: { ctrl: boolean; meta: boolean; prevented: boolean }[] = [];
      (window as unknown as { __keys: typeof seen }).__keys = seen;
      window.addEventListener("keydown", (event) => {
        if (event.key.toLowerCase() === "s") seen.push({ ctrl: event.ctrlKey, meta: event.metaKey, prevented: event.defaultPrevented });
      });
    });
    const keys = (): Promise<{ ctrl: boolean; meta: boolean; prevented: boolean }[]> => page.evaluate(() => (window as unknown as { __keys: { ctrl: boolean; meta: boolean; prevented: boolean }[] }).__keys);
    const allow = page.getByRole("textbox", { name: "Allow, item 1" });
    const saved = (): string[] => (json(fresh, "send_policy.json") as { allow: string[] }).allow;
    const original = saved()[0];
    await allow.fill("Saved with Control");

    // Outside the section (the menu): the key keeps its meaning and nothing is saved.
    await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: /^Webhooks/ }).focus();
    await page.keyboard.press("Control+s");
    expect((await keys()).at(-1)).toEqual({ ctrl: true, meta: false, prevented: false });
    expect(saved()[0]).toBe(original);
    await expect(page.locator("[data-save-bar]")).toBeVisible();

    // Inside: Ctrl+S saves and prevents.
    await allow.focus();
    await page.keyboard.press("Control+s");
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    expect(saved()[0]).toBe("Saved with Control");
    expect((await keys()).at(-1)).toEqual({ ctrl: true, meta: false, prevented: true });

    // Cmd+S, the same.
    await allow.fill("Saved with Meta");
    await page.keyboard.press("Meta+s");
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    expect(saved()[0]).toBe("Saved with Meta");
    expect((await keys()).at(-1)).toEqual({ ctrl: false, meta: true, prevented: true });

    // From a button of the save bar itself the shortcut counts too.
    await allow.fill("Saved from the bar");
    await page.getByRole("button", { name: "Discard changes" }).focus();
    await page.keyboard.press("Control+s");
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    expect(saved()[0]).toBe("Saved from the bar");
  });
});

test("UXP-23 Restore defaults lives in the More actions menu of the section header, with the same confirmation as before", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json"] }, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await fresh.signIn(page);
    await openSection(page, fresh, "send_policy");
    // Not on the page or in a bar: only in the menu.
    await expect(page.getByRole("button", { name: "Restore defaults" })).toHaveCount(0);
    const more = page.getByRole("banner").getByRole("button", { name: "More actions" });
    await expect(more).toBeVisible();
    await more.click();
    await expect(page.getByRole("menuitem", { name: "Restore defaults" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menuitem")).toHaveCount(0);
    // The confirmation says what will happen and can be cancelled without a change.
    await more.click();
    await page.getByRole("menuitem", { name: "Restore defaults" }).click();
    const ask = page.getByRole("alertdialog");
    await expect(ask).toContainText("Restore the defaults of this section?");
    await expect(ask).toContainText("Every saved value of Send policy goes back to its default.");
    await expect(ask).toContainText("The values are removed when you save.");
    await ask.getByRole("button", { name: "Cancel" }).click();
    await expect(ask).toHaveCount(0);
    await expect(page.getByRole("radio", { name: "allowlist" })).toBeChecked();
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    // Confirming puts the defaults in the form (an edit to save), and the file waits for Save.
    const before = fresh.read("send_policy.json");
    await more.click();
    await page.getByRole("menuitem", { name: "Restore defaults" }).click();
    await confirm(page, "Restore defaults");
    await expect(page.getByRole("radio", { name: "denylist" })).toBeChecked();
    await expect(page.locator("[data-save-bar]")).toBeVisible();
    expect(fresh.read("send_policy.json")).toBe(before);
  });
});

test("UXP-24 the section header shows the title, the one-line description and, while a restart waits, the Restart needed badge", async ({ page }) => {
  await withHarness({ seed: ["ui_settings.json"] }, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await fresh.signIn(page);
    const sections = (await (await page.request.get(`${fresh.url}/api/sections`)).json()) as { id: string; title: string; description: string }[];
    const media = sections.find((entry) => entry.id === "media");
    expect(media?.description).toBeTruthy();
    await openSection(page, fresh, "media");
    const header = page.getByRole("banner");
    await expect(header.getByRole("heading", { level: 1 })).toHaveText(media?.title ?? "");
    await expect(header.getByText(media?.description ?? "", { exact: true })).toBeVisible();
    // One line each: the header is a fixed height and the texts are cut, not wrapped.
    const lines = await header.locator("h1, p").evaluateAll((nodes) => nodes.map((node) => ({ wrap: getComputedStyle(node).whiteSpace, height: node.getBoundingClientRect().height, line: parseFloat(getComputedStyle(node).lineHeight) })));
    for (const line of lines) {
      expect(line.wrap).toBe("nowrap");
      expect(line.height).toBeLessThanOrEqual(line.line + 1);
    }
    await expect(header.getByText("Restart needed")).toHaveCount(0);
    await page.getByRole("switch", { name: "Save incoming media" }).click();
    await saveButton(page).click();
    await expect(header.getByText("Restart needed")).toBeVisible();
    // Another section has no badge; coming back to the first shows it again (the server remembers the restart).
    await openSection(page, fresh, "privacy");
    await expect(header.getByText("Restart needed")).toHaveCount(0);
    await openSection(page, fresh, "media");
    await expect(header.getByText("Restart needed")).toBeVisible();
  });
});

test("UXP-25 a successful save hides the bar and shows Saved once as a toast", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json"] }, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await fresh.signIn(page);
    await openSection(page, fresh, "send_policy");
    await page.getByRole("textbox", { name: "Allow, item 1" }).fill("Saved once");
    const toasts = page.getByRole("region", { name: "Notifications" }).getByText("Changes saved.");
    await expect(toasts).toHaveCount(0);
    await saveButton(page).click();
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    await expect(toasts).toHaveCount(1);
    await expect(toasts.first()).toBeVisible();
    // Not a second time while the bar is gone and nothing else happens.
    await expect.poll(async () => toasts.count(), { timeout: 2500, intervals: [250] }).toBe(1);
    expect((json(fresh, "send_policy.json") as { allow: string[] }).allow[0]).toBe("Saved once");
    // The toast floats above the bar: saving again at once, the Save button is not under it.
    await page.getByRole("textbox", { name: "Allow, item 1" }).fill("Saved twice");
    await expect(saveButton(page)).toBeVisible();
    const buttonBox = await saveButton(page).boundingBox();
    // The toast slides in; once it rests its bottom edge is above the bar.
    await expect
      .poll(async () => {
        const box = await page.locator("[data-sonner-toast]").first().boundingBox();
        return box === null ? 9999 : box.y + box.height;
      })
      .toBeLessThanOrEqual((buttonBox?.y ?? 0) + 1);
    await saveButton(page).click();
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    expect((json(fresh, "send_policy.json") as { allow: string[] }).allow[0]).toBe("Saved twice");
  });
});

/** The seconds in "Updated N s ago", or -1 for any other text. */
async function secondsOf(label: Locator): Promise<number> {
  const text = (await label.textContent()) ?? "";
  const found = /^Updated (\d+) s ago$/.exec(text);
  return found?.[1] === undefined ? -1 : Number(found[1]);
}

test("UXP-26 Status and Drafts and audit show Updated N s ago, counting up each second and in minutes after 60 s, with a Refresh button", async ({ page }) => {
  test.setTimeout(60_000);
  await page.clock.install();
  await harness.current.signIn(page);
  for (const path of ["/status", "/activity"]) {
    await page.goto(`${harness.current.url}${path}`);
    const label = page.locator("[data-freshness]");
    await expect(label).toHaveText(/^Updated \d+ s ago$/);
    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
    // It grows by the second (the page's own clock runs ahead of the fake one by what we move it).
    const first = await secondsOf(label);
    expect(first).toBeGreaterThanOrEqual(0);
    await page.clock.runFor(3000);
    await expect.poll(() => secondsOf(label)).toBeGreaterThanOrEqual(first + 3);
    // The server cannot be asked again, so the age is only the clock's: after 75 s it is told in minutes.
    await page.route("**/api/ops/**", (route) => route.abort());
    await page.clock.runFor(75_000);
    await expect(label).toHaveText(/^Updated 1 min ago$/);
    await page.clock.runFor(60_000 * 3);
    await expect(label).toHaveText(/^Updated 4 min ago$/);
    await page.unroute("**/api/ops/**");
  }
});

test("UXP-27 Refresh fetches again at once and the text reads Updated 0 s ago", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  for (const [path, request] of [["/status", "GET /api/status"], ["/activity", ""]] as const) {
    await page.goto(`${harness.current.url}${path}`);
    const label = page.locator("[data-freshness]");
    await expect(label).toHaveText(/^Updated \d+ s ago$/);
    await expect.poll(() => secondsOf(label), { timeout: 8000 }).toBeGreaterThanOrEqual(2);
    const asked: string[] = [];
    page.on("request", (call) => asked.push(call.url()));
    const bridgeBefore = harness.current.bridge.requests.filter((line) => line === request).length;
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(label).toHaveText("Updated 0 s ago");
    // The page asked its own API again, and for Status the bridge too.
    expect(asked.filter((url) => /\/api\/ops\//.test(url)).length).toBeGreaterThan(0);
    if (request !== "") expect(harness.current.bridge.requests.filter((line) => line === request).length).toBeGreaterThan(bridgeBefore);
    page.removeAllListeners("request");
  }
});

test("UXP-28 when the last fetch failed Could not update shows next to the last good time and the earlier data stays", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  // Status: the interface's own API stops answering, then answers again.
  await page.goto(`${harness.current.url}/status`);
  await expect(page.getByText("Paired with a phone")).toBeVisible();
  await expect(page.getByText("Could not update")).toHaveCount(0);
  await page.route("**/api/ops/**", (route) => route.abort());
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Could not update")).toBeVisible();
  await expect(page.locator("[data-freshness]")).toHaveText(/^Updated \d+ s ago$/);
  await expect(page.getByText("Paired with a phone")).toBeVisible();
  await expect(page.getByText("5500000000000")).toBeVisible();
  await page.unroute("**/api/ops/**");
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Could not update")).toHaveCount(0);
  // Drafts and audit: the 45 rows stay while the refresh fails.
  await page.goto(`${harness.current.url}/activity`);
  const table = page.getByRole("table", { name: "Send audit table" });
  await expect(table.locator("tbody tr")).toHaveCount(45);
  await page.route("**/api/ops/**", (route) => route.abort());
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Could not update")).toBeVisible();
  await expect(table.locator("tbody tr")).toHaveCount(45);
  await expect(page.locator("[data-freshness]")).toHaveText(/^Updated \d+ s ago$/);
  await page.unroute("**/api/ops/**");
});

test("UXP-29 with no drafts and no audit rows each list shows an empty state with an icon, a title and one sentence that says what fills it", async ({ page }) => {
  await withHarness({}, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await fresh.signIn(page);
    await page.goto(`${fresh.url}/activity`);
    const expected = [
      ["Pending drafts", "No drafts are waiting.", "Drafts made by the monitor appear here until they are sent or discarded elsewhere."],
      ["Send audit", "No sends are logged yet.", "Every message sent through the bridge is logged here, with the rule that allowed or blocked it."],
      ["Monitor audit", "No monitor replies are logged yet.", "Each reply the monitor makes, or tries to make, is logged here."],
    ];
    for (const [card = "", title = "", sentence = ""] of expected) {
      const region = page.getByRole("region", { name: card, exact: true });
      await expect(region.getByRole("heading", { level: 3, name: title })).toBeVisible();
      await expect(region.getByText(sentence, { exact: true })).toBeVisible();
      await expect(region.locator("svg[aria-hidden=true]")).toHaveCount(1);
      await expect(region.getByRole("table")).toHaveCount(0);
    }
  });
});

test("UXP-30 every time is relative text with the exact UTC time in title and in the datetime of a time element", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  const check = async (): Promise<number> => {
    const rows = await page.locator("time").evaluateAll((nodes) =>
      nodes.map((node) => ({ text: node.textContent.trim(), title: node.getAttribute("title") ?? "", datetime: node.getAttribute("datetime") ?? "" })),
    );
    for (const row of rows) {
      expect(row.text).toMatch(/^(in )?\d+ (s|min|h|d)( ago)?$/);
      expect(row.title).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$/);
      expect(row.datetime).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
      expect(row.title).toBe(`${row.datetime.slice(0, 10)} ${row.datetime.slice(11, 19)} UTC`);
    }
    return rows.length;
  };
  await page.goto(`${harness.current.url}/status`);
  await expect(page.getByText("Paired with a phone")).toBeVisible();
  expect(await check()).toBeGreaterThanOrEqual(5);
  // A known moment: the fake bridge says it started at 2026-01-02 03:04:05 UTC.
  const started = page.locator('time[datetime="2026-01-02T03:04:05.000Z"]');
  await expect(started).toHaveAttribute("title", "2026-01-02 03:04:05 UTC");
  await expect(started).toHaveText(/^\d+ d ago$/);
  // No absolute stamp is shown as text.
  expect(await page.locator("#content").innerText()).not.toMatch(/\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/);
  await page.goto(`${harness.current.url}/activity`);
  await expect(page.getByRole("table", { name: "Send audit table" }).locator("tbody tr")).toHaveCount(45);
  expect(await check()).toBeGreaterThan(100);
  expect(await page.locator("#content").innerText()).not.toMatch(/\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/);
  // The audit rows are three hours old, down to the minute: the first row reads "3 h ago".
  await expect(page.getByRole("table", { name: "Send audit table" }).locator("tbody tr").first().locator("time")).toHaveText("3 h ago");
});

test("UXP-31 a copy button next to the phone number or the bridge version copies the value and shows Copied as a toast", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: harness.current.url });
    const page = await context.newPage();
    await harness.current.signIn(page);
    await page.goto(`${harness.current.url}/status`);
    const toasts = page.getByRole("region", { name: "Notifications" }).getByText("Copied", { exact: true });
    await page.getByRole("button", { name: "Copy phone" }).click();
    await expect(toasts).toHaveCount(1);
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("5500000000000");
    await page.getByRole("button", { name: "Copy bridge version" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("0.0.0-test");
    await expect(toasts.first()).toBeVisible();
    // A browser that refuses says so instead of claiming it copied.
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { value: { writeText: () => Promise.reject(new Error("denied")) }, configurable: true });
    });
    await page.getByRole("button", { name: "Copy phone" }).click();
    await expect(page.getByRole("region", { name: "Notifications" }).getByText(/^Could not copy/)).toBeVisible();
  } finally {
    await context.close();
  }
});

test("UXP-32 the theme button is named by the theme it switches to and shows that name as a tooltip", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await harness.current.signIn(page);
  await page.evaluate(() => {
    localStorage.clear();
  });
  await page.reload();
  for (const [current, next] of [["System", "Light"], ["Light", "Dark"], ["Dark", "System"]] as const) {
    const name = `Switch to ${next} theme`;
    // The last click left the pointer on the button, and a click closes its tooltip: leave and come back.
    await page.mouse.move(700, 500, { steps: 12 });
    const button = page.getByRole("button", { name }).first();
    await expect(button, `from ${current}`).toBeVisible();
    await expect(page.getByRole("button", { name: /^Switch to \w+ theme$/ })).toHaveCount(1);
    await button.hover();
    await expect(page.getByRole("tooltip")).toHaveText(name);
    await page.mouse.move(700, 500, { steps: 12 });
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    // The keyboard shows it too.
    await page.keyboard.press("Tab");
    await button.focus();
    await expect(page.getByRole("tooltip")).toHaveText(name);
    await button.click();
  }
  await expect(page.getByRole("button", { name: "Switch to Light theme" })).toBeVisible();
});

test("UXP-33 with reduced motion no transition or animation runs longer than 1 ms, and without it the buttons do animate", async ({ page }) => {
  test.setTimeout(60_000);
  const longest = (): Promise<{ transition: number; animation: number; sample: string }> =>
    page.evaluate(() => {
      const seconds = (value: string): number => Math.max(0, ...value.split(",").map((part) => parseFloat(part) * (part.trim().endsWith("ms") ? 0.001 : 1)));
      let transition = 0;
      let animation = 0;
      let sample = "";
      for (const node of Array.from(document.querySelectorAll("*"))) {
        const css = getComputedStyle(node);
        const slow = seconds(css.transitionDuration);
        const spin = css.animationName === "none" ? 0 : seconds(css.animationDuration);
        if (slow > transition) {
          transition = slow;
          sample = `${node.tagName} ${String(node.getAttribute("class")).slice(0, 40)}`;
        }
        animation = Math.max(animation, spin);
      }
      return { transition, animation, sample };
    });
  await page.setViewportSize({ width: 390, height: 844 });
  await harness.current.signIn(page);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto(`${harness.current.url}/s/send_policy`);
  await expect(page.getByRole("textbox", { name: "Allow, item 1" })).toBeVisible();
  expect((await longest()).transition, "without the preference the buttons ease their colors").toBeGreaterThan(0.1);
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const path of ["/s/send_policy", "/status", "/activity", "/s/monitor"]) {
    await page.goto(`${harness.current.url}${path}`);
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    const motion = await longest();
    expect(motion.transition, `${path}: ${motion.sample}`).toBeLessThanOrEqual(0.001);
    expect(motion.animation, path).toBeLessThanOrEqual(0.001);
  }
  // The drawer's slide is one of those animations.
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(page.getByRole("dialog", { name: "Sections menu" })).toBeVisible();
  const drawer = await longest();
  expect(drawer.transition).toBeLessThanOrEqual(0.001);
  expect(drawer.animation).toBeLessThanOrEqual(0.001);
  expect(await page.getByRole("dialog").evaluate((node) => parseFloat(getComputedStyle(node).animationDuration))).toBeLessThanOrEqual(0.001);
});

const PHONE_WIDTHS = [360, 390, 599];

test("UXP-35 under 600 px every button, link, switch, radio, checkbox and icon button has a hit area of at least 44 by 44 px, on every page, in the drawer and in the dirty form", async ({ page }) => {
  test.setTimeout(120_000);
  await harness.current.signIn(page);
  for (const width of PHONE_WIDTHS) {
    await page.setViewportSize({ width, height: 800 });
    for (const path of PAGES) {
      await page.goto(`${harness.current.url}${path}`);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      if (path.startsWith("/s/")) await expect(page.locator("form [data-field]").first()).toBeVisible();
      else await expect(page.locator("#content h2").first()).toBeVisible();
      expect(await smallTargets(page), `${path} at ${String(width)} px`).toEqual([]);
    }
  }
  await page.setViewportSize({ width: 360, height: 800 });
  // The drawer: its entries, the filter's clear button and the header buttons.
  await page.goto(`${harness.current.url}/status`);
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(page.getByRole("dialog", { name: "Sections menu" })).toBeVisible();
  const entries = await page.getByRole("dialog").getByRole("link").count();
  expect(entries).toBeGreaterThan(10);
  expect(await smallTargets(page), "the open drawer").toEqual([]);
  await page.getByRole("dialog").getByRole("searchbox").fill("zzzz");
  await expect(page.getByRole("dialog").getByRole("button", { name: "Clear filter" })).toBeVisible();
  expect(await smallTargets(page), "the drawer with no match").toEqual([]);
  await page.keyboard.press("Escape");
  // A dirty form: the save bar, the item buttons, the more-actions menu and its item, a dialog.
  await page.goto(`${harness.current.url}/s/send_policy`);
  await page.getByRole("textbox", { name: "Allow, item 1" }).fill("hit areas");
  await expect(page.locator("[data-save-bar]")).toBeVisible();
  expect(await smallTargets(page), "the dirty form").toEqual([]);
  await page.getByRole("button", { name: "More actions" }).click();
  await expect(page.getByRole("menuitem", { name: "Restore defaults" })).toBeVisible();
  expect(await smallTargets(page), "the open menu").toEqual([]);
  await page.getByRole("menuitem", { name: "Restore defaults" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  expect(await smallTargets(page), "a confirmation").toEqual([]);
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
});

test("UXP-35 the sign-in page also has hit areas of 44 px under 600 px", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 700 });
  await page.goto(`${harness.current.url}/login`);
  await expect(page.getByLabel("Password")).toBeVisible();
  expect(await smallTargets(page)).toEqual([]);
});

test("UXP-36 under 600 px every text field has a font size of at least 16 px", async ({ page }) => {
  test.setTimeout(90_000);
  const fieldSizes = (): Promise<string[]> =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>("input, textarea, select"))
        .filter((node) => !(node instanceof HTMLInputElement && ["checkbox", "radio", "hidden"].includes(node.type)))
        .filter((node) => node.closest("[aria-hidden=true]") === null && node.getBoundingClientRect().width > 1)
        .filter((node) => parseFloat(getComputedStyle(node).fontSize) < 16)
        .map((node) => `${node.tagName} ${node.getAttribute("aria-label") ?? node.id} ${getComputedStyle(node).fontSize}`),
    );
  await page.setViewportSize({ width: 360, height: 700 });
  await page.goto(`${harness.current.url}/login`);
  await expect(page.getByLabel("Password")).toBeVisible();
  expect(await fieldSizes(), "login").toEqual([]);
  await harness.current.signIn(page);
  for (const width of PHONE_WIDTHS) {
    await page.setViewportSize({ width, height: 800 });
    for (const path of PAGES) {
      await page.goto(`${harness.current.url}${path}`);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      if (path.startsWith("/s/")) await expect(page.locator("form [data-field]").first()).toBeVisible();
      expect(await fieldSizes(), `${path} at ${String(width)}`).toEqual([]);
    }
  }
  // The fields that exist only after an action: an added item, the drawer's filter, a secret field.
  await page.goto(`${harness.current.url}/s/send_policy`);
  await field(page, "allow").getByRole("button", { name: "Add item" }).click();
  await page.getByRole("textbox", { name: "Allow, item 3" }).fill("x");
  expect(await fieldSizes(), "an added item").toEqual([]);
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(page.getByRole("dialog").getByRole("searchbox")).toBeVisible();
  expect(await fieldSizes(), "the drawer filter").toEqual([]);
  expect(await page.getByRole("searchbox", { name: "Filter sections" }).evaluate((node) => parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(16);
});

test("UXP-37 under 600 px form cards are one full-width column with 16 px gutters, Status cards one column and the counters two columns", async ({ page }) => {
  await harness.current.signIn(page);
  for (const width of PHONE_WIDTHS) {
    await page.setViewportSize({ width, height: 800 });
    for (const id of ["send_policy", "channel", "monitor", "webhooks"]) {
      await openSection(page, harness.current, id);
      const boxes = await cards(page).evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect()).map((box) => ({ x: box.x, y: box.y, w: box.width })));
      expect(boxes.length, id).toBeGreaterThan(0);
      boxes.forEach((box, index) => {
        expect(box.x, `${id} card ${String(index)} at ${String(width)}`).toBe(16);
        expect(box.w, `${id} card ${String(index)} at ${String(width)}`).toBe(width - 32);
        if (index > 0) expect(box.y).toBeGreaterThan(boxes[index - 1]?.y ?? 0);
      });
    }
    await page.goto(`${harness.current.url}/status`);
    await expect(page.getByRole("region", { name: "Counters", exact: true })).toBeVisible();
    const status = await cards(page).evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect()).map((box) => ({ x: box.x, y: box.y, w: box.width })));
    expect(status.length).toBe(3);
    status.forEach((box, index) => {
      expect(box.x).toBe(16);
      expect(box.w).toBe(width - 32);
      if (index > 0) expect(box.y).toBeGreaterThan(status[index - 1]?.y ?? 0);
    });
    // The counters: tiles in exactly two columns.
    const tiles = await page.getByRole("region", { name: "Counters", exact: true }).locator("dl > div").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().x));
    expect(tiles.length).toBeGreaterThanOrEqual(8);
    expect(new Set(tiles.map((left) => Math.round(left))).size, `counters at ${String(width)}`).toBe(2);
  }
});

test("UXP-38 under 600 px a table that does not fit scrolls sideways inside its own region with the first column kept in view, and the page has no horizontal scroll", async ({ page }) => {
  await harness.current.signIn(page);
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`${harness.current.url}/activity`);
    for (const title of ["Pending drafts", "Send audit", "Monitor audit"]) {
      const region = page.getByRole("region", { name: `${title} table` });
      await region.scrollIntoViewIfNeeded();
      const facts = await region.evaluate((node) => ({ wide: node.scrollWidth - node.clientWidth, tab: node.getAttribute("tabindex") }));
      expect(facts.wide, `${title} at ${String(width)}`).toBeGreaterThan(50);
      expect(facts.tab).toBe("0");
      const first = region.locator("tbody tr").first().locator("td").first();
      const head = region.locator("thead th").first();
      const edge = (await region.boundingBox())?.x ?? 0;
      const before = { cell: (await first.boundingBox())?.x ?? -1, head: (await head.boundingBox())?.x ?? -1 };
      await region.evaluate((node) => {
        node.scrollLeft = node.scrollWidth;
      });
      expect(await region.evaluate((node) => node.scrollLeft), "it scrolled sideways").toBeGreaterThan(50);
      const after = { cell: (await first.boundingBox())?.x ?? -2, head: (await head.boundingBox())?.x ?? -2 };
      expect(after.cell, "the first column stays").toBeCloseTo(before.cell, 0);
      expect(after.head).toBeCloseTo(before.head, 0);
      expect(after.cell).toBeLessThanOrEqual(edge + 2);
      expect(await first.evaluate((node) => getComputedStyle(node).position)).toBe("sticky");
      // The last column came into view.
      const last = (await region.locator("thead th").last().boundingBox()) ?? { x: 0, width: 0 };
      expect(last.x + last.width).toBeLessThanOrEqual(edge + (await region.evaluate((node) => node.clientWidth)) + 2);
      const scroll = await documentScrolls(page);
      expect(scroll.horizontal).toBeLessThanOrEqual(0);
      expect(await page.evaluate(() => window.scrollX)).toBe(0);
      expect(await page.locator("#content").evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
    }
  }
});

/** A stand-in for window.visualViewport that the test drives; the bar reads it to find the keyboard. */
async function fakeVisualViewport(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const target = new EventTarget() as EventTarget & { height: number; offsetTop: number; width: number; scale: number };
    target.height = window.innerHeight;
    target.offsetTop = 0;
    target.width = window.innerWidth;
    target.scale = 1;
    Object.defineProperty(window, "visualViewport", { value: target, configurable: true });
    (window as unknown as { __vv: typeof target }).__vv = target;
  });
}

test("UXP-39 under 600 px the save bar is fixed at the bottom, above the keyboard and the bottom safe area, with its buttons side by side and at least 44 px tall", async ({ browser }) => {
  const context = await browser.newContext({ viewport: PHONE, hasTouch: true, isMobile: true });
  try {
    const page = await context.newPage();
    await fakeVisualViewport(page);
    await harness.current.signIn(page);
    await page.goto(`${harness.current.url}/s/send_policy`);
    await expect(page.locator("form [data-field]").first()).toBeVisible();
    await expect(page.locator("[data-save-bar]")).toHaveCount(0);
    await page.getByRole("textbox", { name: "Allow, item 1" }).fill("a draft");
    const bar = page.locator("[data-save-bar]");
    await expect(bar).toBeVisible();
    expect(await bar.evaluate((node) => getComputedStyle(node).position)).toBe("fixed");
    const edges = async (): Promise<{ bottom: number; left: number; right: number }> => {
      const box = (await bar.boundingBox()) ?? { x: 0, y: 0, width: 0, height: 0 };
      return { bottom: Math.round(box.y + box.height), left: box.x, right: box.x + box.width };
    };
    expect(await edges()).toEqual({ bottom: PHONE.height, left: 0, right: PHONE.width });
    // Buttons side by side, each at least 44 px tall.
    const discard = await bar.getByRole("button", { name: "Discard changes" }).boundingBox();
    const save = await bar.getByRole("button", { name: "Save", exact: true }).boundingBox();
    expect(discard?.y).toBeCloseTo(save?.y ?? -1, 0);
    expect(discard?.x).toBeLessThan(save?.x ?? 0);
    expect((discard?.x ?? 0) + (discard?.width ?? 0)).toBeLessThanOrEqual((save?.x ?? 0) + 1);
    expect(discard?.height).toBeGreaterThanOrEqual(44);
    expect(save?.height).toBeGreaterThanOrEqual(44);
    // The bar does not scroll with the content.
    await page.locator("#content").evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    expect((await edges()).bottom).toBe(PHONE.height);
    // The last of the content can scroll clear of it.
    const lastCard = await cards(page).last().boundingBox();
    expect((lastCard?.y ?? 0) + (lastCard?.height ?? 0)).toBeLessThanOrEqual((await bar.boundingBox())?.y ?? 0);
    // The keyboard, as the page's fallback sees it: the visual viewport is 300 px shorter than the window.
    await page.evaluate(() => {
      const viewport = (window as unknown as { __vv: EventTarget & { height: number } }).__vv;
      viewport.height = window.innerHeight - 300;
      viewport.dispatchEvent(new Event("resize"));
    });
    await expect.poll(async () => (await edges()).bottom).toBe(PHONE.height - 300);
    // And back.
    await page.evaluate(() => {
      const viewport = (window as unknown as { __vv: EventTarget & { height: number } }).__vv;
      viewport.height = window.innerHeight;
      viewport.dispatchEvent(new Event("resize"));
    });
    await expect.poll(async () => (await edges()).bottom).toBe(PHONE.height);
    // The other model of keyboard (the window itself gets shorter, interactive-widget=resizes-content): still at the bottom.
    await page.setViewportSize({ width: PHONE.width, height: 544 });
    await expect.poll(async () => (await edges()).bottom).toBe(544);
    await page.setViewportSize(PHONE);
    // The bottom safe area: the buttons clear the home indicator.
    await page.evaluate(() => {
      document.documentElement.style.setProperty("--safe-bottom", "34px");
    });
    await expect.poll(async () => Math.round(((await bar.boundingBox())?.y ?? 0) + ((await bar.boundingBox())?.height ?? 0))).toBe(PHONE.height);
    const buttons = await bar.getByRole("button", { name: "Save", exact: true }).boundingBox();
    expect((buttons?.y ?? 0) + (buttons?.height ?? 0)).toBeLessThanOrEqual(PHONE.height - 34 + 1);
    // The viewport meta lets the page draw under the notch so that the safe-area variables mean something.
    expect(await page.locator('meta[name="viewport"]').getAttribute("content")).toContain("viewport-fit=cover");
  } finally {
    await context.close();
  }
});

test("UXP-40 the page keeps clear of the safe areas on all four sides and follows the dynamic viewport height", async ({ browser }) => {
  const { context, page } = await phoneOf(browser, harness.current);
  try {
    await page.goto(`${harness.current.url}/s/send_policy`);
    await expect(page.locator("form [data-field]").first()).toBeVisible();
    expect(await page.evaluate(() => CSS.supports("height", "100dvh"))).toBe(true);
    // Without insets, the header starts at the top and the content at the left edge.
    expect((await page.getByRole("banner").boundingBox())?.y).toBe(0);
    // A phone with a notch and a home indicator: top 44, right 30, bottom 34, left 20 (landscape-like values on all sides).
    await page.evaluate(() => {
      const style = document.documentElement.style;
      style.setProperty("--safe-top", "44px");
      style.setProperty("--safe-right", "30px");
      style.setProperty("--safe-bottom", "34px");
      style.setProperty("--safe-left", "20px");
    });
    const header = await page.getByRole("banner").boundingBox();
    const main = await page.getByRole("main").boundingBox();
    expect(header?.y).toBe(44);
    expect(header?.x).toBe(20);
    expect((header?.x ?? 0) + (header?.width ?? 0)).toBe(PHONE.width - 30);
    expect((main?.y ?? 0) + (main?.height ?? 0)).toBe(PHONE.height - 34);
    const card = await cards(page).first().boundingBox();
    expect(card?.x).toBeGreaterThanOrEqual(20);
    expect((card?.x ?? 0) + (card?.width ?? 0)).toBeLessThanOrEqual(PHONE.width - 30);
    // The drawer keeps clear too.
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("dialog", { name: "Sections menu" });
    await expect(drawer).toBeVisible();
    await expect.poll(async () => (await drawer.boundingBox())?.x).toBe(0);
    const title = await drawer.getByText("WhatsApp MCP", { exact: true }).boundingBox();
    expect(title?.y).toBeGreaterThanOrEqual(44);
    expect(title?.x).toBeGreaterThanOrEqual(20);
    const lastEntry = await drawer.getByRole("link").last().evaluate((node) => {
      node.scrollIntoView({ block: "end" });
      return node.getBoundingClientRect().bottom;
    });
    expect(lastEntry).toBeLessThanOrEqual(PHONE.height - 34 + 1);
    await page.keyboard.press("Escape");
    // The dynamic viewport: when the browser bars come or go the window height changes and the shell follows it exactly.
    await page.evaluate(() => {
      const style = document.documentElement.style;
      for (const name of ["--safe-top", "--safe-right", "--safe-bottom", "--safe-left"]) style.removeProperty(name);
    });
    for (const height of [844, 760, 640]) {
      await page.setViewportSize({ width: PHONE.width, height });
      await expect.poll(async () => (await shell(page).boundingBox())?.height).toBe(height);
      expect((await documentScrolls(page)).vertical).toBeLessThanOrEqual(0);
    }
  } finally {
    await context.close();
  }
});

test("UXP-41 turning the device between portrait and landscape keeps the open section, the content's scroll position and the unsaved draft", async ({ browser }) => {
  const { context, page } = await phoneOf(browser, harness.current);
  try {
    await page.goto(`${harness.current.url}/s/channel`);
    const excerpt = page.getByRole("textbox", { name: "Excerpt length" });
    await excerpt.fill("77");
    await expect(page.locator("[data-save-bar]")).toBeVisible();
    const content = page.locator("#content");
    await content.evaluate((node) => {
      node.scrollTop = 200;
    });
    expect(await content.evaluate((node) => node.scrollTop)).toBe(200);
    for (const size of [{ width: 844, height: 390 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
      await page.setViewportSize(size);
      await expect(page).toHaveURL(/\/s\/channel$/);
      await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Claude Code channel" })).toBeVisible();
      await expect(page.getByRole("textbox", { name: "Excerpt length" }), `${String(size.width)}x${String(size.height)}`).toHaveValue("77");
      await expect(page.locator("[data-save-bar]")).toBeVisible();
      // The browser's scroll anchoring may move the offset by the few pixels the cards above grew or shrank; it never resets it.
      expect(Math.abs((await content.evaluate((node) => node.scrollTop)) - 200), `scroll at ${String(size.width)}x${String(size.height)}`).toBeLessThanOrEqual(30);
    }
    // Landscape is 844 px wide: the rail and the section side by side, and the draft is still the form's.
    await expect(panel(page)).toBeVisible();
    expect(await panel(page).evaluate((node) => node.getBoundingClientRect().width)).toBe(72);
  } finally {
    await context.close();
  }
});

test("UXP-43 the viewport meta allows zooming, and at 200% (180 px of layout) nothing overflows sideways or is cut on any page", async ({ browser, page }) => {
  test.setTimeout(120_000);
  await page.goto(`${harness.current.url}/login`);
  const meta = (await page.locator('meta[name="viewport"]').getAttribute("content")) ?? "";
  expect(meta).toContain("width=device-width");
  expect(meta).toContain("initial-scale=1");
  expect(meta).not.toMatch(/user-scalable\s*=\s*(no|0)/);
  expect(meta).not.toMatch(/maximum-scale/);
  expect(meta).not.toMatch(/minimum-scale\s*=\s*1\.?0*$/);
  // A phone at 360 px zoomed to 200% lays the page out in 180 px.
  const context = await browser.newContext({ viewport: { width: 180, height: 320 }, hasTouch: true, isMobile: true });
  try {
    const small = await context.newPage();
    await harness.current.signIn(small);
    for (const path of PAGES) {
      await small.goto(`${harness.current.url}${path}`);
      await expect(small.getByRole("heading", { level: 1 }).first()).toBeVisible();
      if (path.startsWith("/s/")) await expect(small.locator("form [data-field]").first()).toBeVisible();
      else await expect(small.locator("#content h2").first()).toBeVisible();
      const scroll = await documentScrolls(small);
      expect(scroll.horizontal, `${path} page`).toBeLessThanOrEqual(0);
      expect(scroll.vertical, `${path} page`).toBeLessThanOrEqual(0);
      // Nothing of the content is clipped by the content box (it hides sideways overflow instead of scrolling it).
      expect(await small.locator("#content").evaluate((node) => node.scrollWidth - node.clientWidth), `${path} content`).toBeLessThanOrEqual(1);
      const header = await small.getByRole("banner").evaluate((node) => node.scrollWidth - node.clientWidth);
      expect(header, `${path} header`).toBeLessThanOrEqual(1);
      // Every control is inside the window or inside a region that scrolls.
      const lost = await small.evaluate(() =>
        Array.from(document.querySelectorAll<HTMLElement>("#content button, #content a[href], #content input, #content textarea"))
          .filter((node) => node.getBoundingClientRect().width > 1 && node.closest("[role=region][tabindex], [aria-hidden=true]") === null)
          .filter((node) => node.getBoundingClientRect().right > window.innerWidth + 1 || node.getBoundingClientRect().left < -1)
          .map((node) => `${node.tagName} ${node.getAttribute("aria-label") ?? node.textContent}`),
      );
      expect(lost, `${path} controls`).toEqual([]);
    }
    // The drawer at 180 px: its entries fit.
    await small.goto(`${harness.current.url}/status`);
    await small.getByRole("button", { name: "Open menu" }).click();
    const drawer = small.getByRole("dialog", { name: "Sections menu" });
    await expect(drawer).toBeVisible();
    expect((await drawer.boundingBox())?.width).toBeLessThanOrEqual(180 * 0.85 + 0.5);
    expect(await drawer.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  } finally {
    await context.close();
  }
});

interface Finding {
  where: string;
  rule: string;
  nodes: string;
}

async function axeOf(page: Page, where: string): Promise<Finding[]> {
  await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; animation: none !important; }" });
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return result.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical" || violation.id === "color-contrast")
    .map((violation) => ({ where, rule: violation.id, nodes: violation.nodes.map((node) => node.target.join(" ")).join(" | ") }));
}

async function bothThemes(page: Page, where: string): Promise<Finding[]> {
  const found: Finding[] = [];
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    found.push(...(await axeOf(page, `${where} [${scheme}]`)));
  }
  return found;
}

test("UXP-14 UXP-21 axe finds no serious violation or contrast failure at 360, 768 and 1280 px in both themes on the cards, the rail, the open drawer and the dirty save bar", async ({ page }) => {
  test.setTimeout(420_000);
  const found: Finding[] = [];
  await harness.current.signIn(page);
  for (const width of [360, 768, 1280]) {
    // Each width starts from the defaults: no stored menu choice.
    await page.evaluate(() => {
      localStorage.clear();
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${harness.current.url}/s/privacy`);
    const at = `${String(width)} px`;
    // Cards: a form, a record list, the live pages.
    for (const path of ["/s/send_policy", "/s/channel", "/s/monitor", "/status", "/activity"]) {
      await page.goto(`${harness.current.url}${path}`);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      if (path.startsWith("/s/")) await expect(page.locator("form [data-field]").first()).toBeVisible();
      else await expect(page.locator("#content h2").first()).toBeVisible();
      found.push(...(await bothThemes(page, `${path} ${at}`)));
    }
    // The error mark on a card.
    await page.goto(`${harness.current.url}/s/channel`);
    await field(page, "excerpt_chars").getByRole("textbox").fill("not a number");
    await saveButton(page).click();
    await expect(page.locator("[data-needs-attention]")).toHaveCount(1);
    found.push(...(await bothThemes(page, `a card marked as needing attention ${at}`)));
    // The dirty save bar.
    await page.goto(`${harness.current.url}/s/send_policy`);
    await page.getByRole("textbox", { name: "Allow, item 1" }).fill("axe");
    await expect(page.locator("[data-save-bar]")).toBeVisible();
    found.push(...(await bothThemes(page, `dirty save bar ${at}`)));
    // The menu: the icon rail (768 px defaults to it, 1280 px after collapsing) and the open drawer (360 px).
    await page.goto(`${harness.current.url}/s/privacy`);
    if (width === 360) {
      await page.getByRole("button", { name: "Open menu" }).click();
      await expect(page.getByRole("dialog", { name: "Sections menu" })).toBeVisible();
      found.push(...(await bothThemes(page, `open drawer ${at}`)));
      await page.getByRole("dialog").getByRole("searchbox").fill("zzzz");
      await expect(page.getByRole("dialog").getByText("No section matches")).toBeVisible();
      found.push(...(await bothThemes(page, `drawer without a match ${at}`)));
      await page.keyboard.press("Escape");
    } else {
      if (width === 1280) await page.getByRole("button", { name: "Collapse menu" }).click();
      await expect(shell(page)).toHaveAttribute("data-menu", "collapsed");
      found.push(...(await bothThemes(page, `icon rail ${at}`)));
      await page.getByRole("button", { name: "Expand menu" }).click();
      await expect(shell(page)).toHaveAttribute("data-menu", "expanded");
      found.push(...(await bothThemes(page, `expanded menu ${at}`)));
    }
  }
  expect(found.map((finding) => `${finding.where}: ${finding.rule}: ${finding.nodes}`)).toEqual([]);
});
