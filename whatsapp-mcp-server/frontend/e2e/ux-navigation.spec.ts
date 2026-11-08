import type { Browser, CDPSession, Locator, Page } from "@playwright/test";
import { expect, test, withHarness } from "./support/fixtures";
import { field, openSection } from "./support/sections";
import { blockStorage, documentScrolls, longDescription, longTitle, menuNav, menuReady, panel, phoneOf, PHONE, shell, uxHarness, widthOf } from "./support/ux";

// UXP-01..13, 34, 42 and 44..48 of the ui-ux-polish spec: the collapsible menu, the one-scroller shell and the phone
// drawer. The interface has two extension sections (one with a very long title), sixteen monitor profiles and 45 rows
// in each log, so the menu, the lists and the tables all have something to scroll.

const harness = uxHarness();
const phonePage = (browser: Browser, size = PHONE) => phoneOf(browser, harness.current, size);

interface Entry {
  id: string;
  title: string;
  description: string;
  kind: string;
}

/** The sections the server lists, read the way the SPA reads them. */
async function sectionsOf(page: Page): Promise<Entry[]> {
  const answer = await page.request.get(`${harness.current.url}/api/sections`);
  expect(answer.ok()).toBe(true);
  return (await answer.json()) as Entry[];
}

test("UXP-01 the Collapse menu button makes a 72 px rail with one icon per section, Expand menu brings the 320 px menu back, by mouse and by keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await harness.current.signIn(page);
  await menuReady(page);
  const entries = await sectionsOf(page);
  const aside = panel(page);
  expect(await widthOf(aside)).toBe(320);
  const collapse = page.getByRole("button", { name: "Collapse menu" });
  await expect(collapse).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Expand menu" })).toHaveCount(0);

  await collapse.click();
  const expand = page.getByRole("button", { name: "Expand menu" });
  await expect(expand).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("button", { name: "Collapse menu" })).toHaveCount(0);
  await expect(shell(page)).toHaveAttribute("data-menu", "collapsed");
  expect(await widthOf(aside)).toBe(72);
  // One icon per section, and no text of the section is drawn.
  const links = menuNav(page).locator("a");
  await expect(links).toHaveCount(entries.length);
  for (let index = 0; index < entries.length; index += 1) {
    const link = links.nth(index);
    await expect(link.locator("svg")).toHaveCount(1);
    await expect(link).toHaveText("");
  }

  await expand.click();
  await expect(page.getByRole("button", { name: "Collapse menu" })).toHaveAttribute("aria-expanded", "true");
  await expect(shell(page)).toHaveAttribute("data-menu", "expanded");
  expect(await widthOf(aside)).toBe(320);

  // The keyboard does the same: focus the button, Enter collapses, Space expands.
  await page.getByRole("button", { name: "Collapse menu" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Expand menu" })).toHaveAttribute("aria-expanded", "false");
  expect(await widthOf(aside)).toBe(72);
  await expect(page.getByRole("button", { name: "Expand menu" })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Collapse menu" })).toHaveAttribute("aria-expanded", "true");
  expect(await widthOf(aside)).toBe(320);
});

test("UXP-02 in the rail every icon is named by its section and shows that name as a tooltip on hover and on keyboard focus; a pending restart adds a dot and its words", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await harness.current.signIn(page);
  await menuReady(page);
  const entries = await sectionsOf(page);
  await page.getByRole("button", { name: "Collapse menu" }).click();
  await expect(shell(page)).toHaveAttribute("data-menu", "collapsed");
  for (const entry of entries) {
    await expect(menuNav(page).getByRole("link", { name: entry.title, exact: true })).toBeVisible();
  }
  // Hover: the pointer shows the name, and leaving hides it. The order is the menu's own (grouped), not the server's.
  const names = await menuNav(page).locator("a").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label") ?? ""));
  const first = { title: names[0] ?? "" };
  const second = { title: names[1] ?? "" };
  expect(first.title).not.toBe("");
  expect(second.title).not.toBe("");
  await menuNav(page).getByRole("link", { name: first.title, exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText(first.title);
  // The tooltip closes when the pointer moves away over several events (a single jump gives it no time to leave).
  await page.mouse.move(700, 400, { steps: 12 });
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  // Keyboard focus: Tab from the collapse button reaches the entries one by one, each with its tooltip.
  await page.getByRole("button", { name: "Expand menu" }).focus();
  await page.keyboard.press("Tab");
  await expect(menuNav(page).getByRole("link", { name: first.title, exact: true })).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText(first.title);
  await page.keyboard.press("Tab");
  await expect(menuNav(page).getByRole("link", { name: second.title, exact: true })).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText(second.title);
});

test("UXP-02 a section with a restart waiting shows a dot on its rail icon and Restart needed in its accessible name", async ({ page }) => {
  await withHarness({ seed: ["ui_settings.json"] }, async (fresh) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await fresh.signIn(page);
    await openSection(page, fresh, "media");
    await page.getByRole("switch", { name: "Save incoming media" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("banner")).toContainText("Restart needed");
    await page.getByRole("button", { name: "Collapse menu" }).click();
    const link = menuNav(page).getByRole("link", { name: "Media downloads: Restart needed" });
    await expect(link).toBeVisible();
    await expect(link.locator('[data-dot="restart"]')).toBeVisible();
    // Another section has neither the dot nor the words.
    const other = menuNav(page).getByRole("link", { name: "Privacy", exact: true });
    await expect(other).toBeVisible();
    await expect(other.locator('[data-dot="restart"]')).toHaveCount(0);
    await link.hover();
    await expect(page.getByRole("tooltip")).toHaveText("Media downloads: Restart needed");
  });
});

/** Opens the sign-in page of a phone-less desktop at a width and returns the menu mode the shell chose. */
async function menuAt(page: Page, width: number): Promise<string | null> {
  await page.setViewportSize({ width, height: 800 });
  await page.goto(`${harness.current.url}/status`);
  await expect(shell(page)).toBeVisible();
  return shell(page).getAttribute("data-menu");
}

test("UXP-03 the chosen menu state comes back after a reload from 600 px, the defaults are expanded from 1024 px and a rail from 600 to 1023, and blocked storage still toggles and uses the default", async ({ page, browser }) => {
  test.setTimeout(90_000);
  await harness.current.signIn(page);
  await page.evaluate(() => {
    localStorage.clear();
  });
  // No stored choice: the width decides.
  for (const [width, expected, panelWidth] of [
    [1280, "expanded", 320],
    [1024, "expanded", 320],
    [1023, "collapsed", 72],
    [768, "collapsed", 72],
    [600, "collapsed", 72],
  ] as const) {
    expect(await menuAt(page, width), `${String(width)} px`).toBe(expected);
    expect(await widthOf(panel(page)), `${String(width)} px`).toBe(panelWidth);
  }
  // A choice is kept: collapse at 1280 and reload; expand at 768 and reload.
  await menuAt(page, 1280);
  await page.getByRole("button", { name: "Collapse menu" }).click();
  await page.reload();
  await expect(shell(page)).toHaveAttribute("data-menu", "collapsed");
  expect(await widthOf(panel(page))).toBe(72);
  expect(await page.evaluate(() => (JSON.parse(localStorage.getItem("wmcp.ui") ?? "{}") as { state?: { menu?: string } }).state?.menu)).toBe("collapsed");
  // The stored choice also rules at other widths from 600 px.
  expect(await menuAt(page, 1024)).toBe("collapsed");
  await page.evaluate(() => {
    localStorage.clear();
  });
  await menuAt(page, 768);
  await page.getByRole("button", { name: "Expand menu" }).click();
  await page.reload();
  await expect(shell(page)).toHaveAttribute("data-menu", "expanded");
  expect(await widthOf(panel(page))).toBe(320);
  expect(await menuAt(page, 600)).toBe("expanded");

  // Blocked storage: the toggle works, the default is back after a reload.
  const blocked = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  try {
    const stuck = await blocked.newPage();
    await blockStorage(stuck);
    expect(await stuck.evaluate(() => {
      try {
        return typeof localStorage;
      } catch {
        return "throws";
      }
    })).toBe("throws");
    await harness.current.signIn(stuck);
    await expect(shell(stuck)).toHaveAttribute("data-menu", "expanded");
    await stuck.getByRole("button", { name: "Collapse menu" }).click();
    await expect(shell(stuck)).toHaveAttribute("data-menu", "collapsed");
    expect(await widthOf(panel(stuck))).toBe(72);
    await stuck.getByRole("button", { name: "Expand menu" }).click();
    await expect(shell(stuck)).toHaveAttribute("data-menu", "expanded");
    await stuck.getByRole("button", { name: "Collapse menu" }).click();
    await stuck.reload();
    await expect(shell(stuck)).toHaveAttribute("data-menu", "expanded");
    expect(await widthOf(panel(stuck))).toBe(320);
  } finally {
    await blocked.close();
  }
});

test("UXP-04 under 600 px there is no collapse button and no rail, the menu is the drawer, and crossing 600 or 1024 px keeps the open section and the unsaved draft", async ({ page }) => {
  await harness.current.signIn(page);
  for (const width of [599, 390, 360]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`${harness.current.url}/s/send_policy`);
    await expect(shell(page)).toHaveAttribute("data-menu", "drawer");
    await expect(page.getByRole("button", { name: "Collapse menu" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Expand menu" })).toHaveCount(0);
    await expect(panel(page)).toHaveCount(0);
    await expect(menuNav(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByRole("dialog", { name: "Sections menu" }).getByRole("navigation", { name: "Sections" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  // The stored choice does not matter on a phone: it is the drawer even after "expanded" was stored.
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${harness.current.url}/s/send_policy`);
  await page.getByRole("button", { name: "Collapse menu" }).click();
  await page.getByRole("button", { name: "Expand menu" }).click();
  await page.setViewportSize({ width: 500, height: 800 });
  await expect(shell(page)).toHaveAttribute("data-menu", "drawer");
  await expect(panel(page)).toHaveCount(0);

  // Crossing the breakpoints keeps the section and the draft.
  await page.setViewportSize({ width: 800, height: 800 });
  await page.goto(`${harness.current.url}/s/send_policy`);
  const allow = page.getByRole("textbox", { name: "Allow, item 1" });
  await allow.fill("Typed across breakpoints");
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
  for (const width of [1280, 1024, 1023, 700, 599, 400, 600, 1100]) {
    await page.setViewportSize({ width, height: 800 });
    await expect(page).toHaveURL(/\/s\/send_policy$/);
    await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Send policy" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Allow, item 1" }), `${String(width)} px`).toHaveValue("Typed across breakpoints");
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
  }
});

test("UXP-05 the sections sit under Overview, Messaging, Safety and data, Preferences and More, and a heading with no entry is left out", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await menuReady(page);
  const entries = await sectionsOf(page);
  const titleOf = (id: string): string => entries.find((entry) => entry.id === id)?.title ?? `missing ${id}`;
  const expected: [string, string[]][] = [
    ["Overview", ["status", "activity"].map(titleOf)],
    ["Messaging", ["send_policy", "monitor", "webhooks", "channel"].map(titleOf)],
    ["Safety and data", ["agent_safety", "database", "media", "privacy"].map(titleOf)],
    ["Preferences", ["timezone", "interface", "account"].map(titleOf)],
    ["More", ["e2e_extra", "e2e_long"].map(titleOf)],
  ];
  const headings = menuNav(page).getByRole("heading", { level: 2 });
  await expect(headings).toHaveText(expected.map(([name]) => name));
  for (const [name, titles] of expected) {
    const group = menuNav(page).getByRole("region", { name });
    const shown = await group.locator("a span.truncate[title]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("title") ?? ""));
    for (const title of titles) expect(shown, name).toContain(title);
    await expect(group.getByRole("link")).toHaveCount(titles.length);
  }
  // The extension section is in More and nowhere else.
  await expect(menuNav(page).getByRole("region", { name: "Messaging" }).getByRole("link", { name: /^Added at runtime/ })).toHaveCount(0);

  // A heading with no entry is omitted: the filter leaves one group.
  await menuNav(page).getByRole("searchbox", { name: "Filter sections" }).fill("time zone");
  await expect(headings).toHaveText(["Preferences"]);
  await menuNav(page).getByRole("searchbox", { name: "Filter sections" }).fill("added at runtime");
  await expect(headings).toHaveText(["More"]);
});

test("UXP-05 without any extension section the heading More is omitted", async ({ page }) => {
  await withHarness({}, async (plain) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await plain.signIn(page);
    await expect(menuNav(page).getByRole("heading", { level: 2 })).toHaveText(["Overview", "Messaging", "Safety and data", "Preferences"]);
    await expect(menuNav(page).getByRole("heading", { name: "More" })).toHaveCount(0);
  });
});

test("UXP-06 a menu with more than 8 entries has the field Filter sections that hides entries by title or description, ignoring case, and an empty result offers Clear filter", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await menuReady(page);
  const entries = await sectionsOf(page);
  expect(entries.length).toBeGreaterThan(8);
  const filter = menuNav(page).getByRole("searchbox", { name: "Filter sections" });
  await expect(filter).toBeVisible();
  const links = menuNav(page).getByRole("link");
  await expect(links).toHaveCount(entries.length);
  // A title, in another case.
  await filter.fill("TiMe ZoNe");
  await expect(links).toHaveCount(1);
  await expect(links.first()).toContainText("Time zone");
  // A description only: the words are in the extension section's description, not in its title.
  await filter.fill("NOT IN THE MENU WHEN");
  await expect(links).toHaveCount(1);
  await expect(links.first()).toContainText("Added at runtime");
  // Several entries at once.
  await filter.fill("e");
  expect(await links.count()).toBeGreaterThan(1);
  expect(await links.count()).toBeLessThan(entries.length + 1);
  // No match: the message, the button, and the button restores everything.
  await filter.fill("zzzz-no-such-section");
  await expect(links).toHaveCount(0);
  await expect(menuNav(page).getByText("No section matches")).toBeVisible();
  await menuNav(page).getByRole("button", { name: "Clear filter" }).click();
  await expect(filter).toHaveValue("");
  await expect(links).toHaveCount(entries.length);
  await expect(menuNav(page).getByText("No section matches")).toHaveCount(0);
  // The rail has no filter: its entries are icons.
  await page.getByRole("button", { name: "Collapse menu" }).click();
  await expect(menuNav(page).getByRole("searchbox", { name: "Filter sections" })).toHaveCount(0);
});

test("UXP-07 the expanded menu is 320 px wide, each entry is one line of title and one line of description cut with an ellipsis, and the full text is in the title attribute", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await menuReady(page);
  expect(await widthOf(panel(page))).toBe(320);
  const long = menuNav(page).getByRole("link", { name: new RegExp(`^${longTitle().slice(0, 20)}`) });
  await expect(long).toBeVisible();
  // The title line: one line, cut with an ellipsis, the whole title in the attribute.
  const title = long.locator("span.truncate").first();
  const description = long.locator("span.truncate").nth(1);
  await expect(title).toHaveAttribute("title", longTitle());
  await expect(description).toHaveAttribute("title", longDescription());
  for (const line of [title, description]) {
    const style = await line.evaluate((node) => {
      const css = getComputedStyle(node);
      return {
        wrap: css.whiteSpace,
        overflow: css.overflowX,
        ellipsis: css.textOverflow,
        cut: node.scrollWidth > node.clientWidth,
        height: node.getBoundingClientRect().height,
        lineHeight: parseFloat(css.lineHeight),
      };
    });
    expect(style.wrap).toBe("nowrap");
    expect(style.overflow).toBe("hidden");
    expect(style.ellipsis).toBe("ellipsis");
    expect(style.cut, "the long text really is cut").toBe(true);
    expect(style.height).toBeLessThanOrEqual(style.lineHeight + 1);
  }
  // The entry never grows beyond the menu: no sideways scroll in it.
  const reach = await menuNav(page).locator("[data-menu-scroll]").evaluate((node) => node.scrollWidth - node.clientWidth);
  expect(reach).toBeLessThanOrEqual(0);
  // Every entry has its own title and description in the attributes, whatever their length.
  const entries = await sectionsOf(page);
  for (const entry of entries) {
    const link = menuNav(page).locator(`a[href="${entry.kind === "section" ? "/s/" : "/"}${entry.id}"]`);
    await expect(link.locator("span.truncate").first()).toHaveAttribute("title", entry.title);
    if (entry.description) await expect(link.locator("span.truncate").nth(1)).toHaveAttribute("title", entry.description);
  }
  // On the page, a long title is cut in the header and the whole text is in its title attribute.
  await page.setViewportSize({ width: 800, height: 900 });
  await page.goto(`${harness.current.url}/s/e2e_long`);
  const heading = page.getByRole("banner").getByRole("heading", { level: 1 });
  await expect(heading).toHaveAttribute("title", longTitle());
  expect(await heading.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
  expect(await heading.evaluate((node) => getComputedStyle(node).textOverflow)).toBe("ellipsis");
});

const SHELL_PAGES = ["/status", "/activity", "/s/monitor", "/s/send_policy", "/s/channel", "/s/database", "/s/webhooks", "/account"];

test("UXP-08 the document never scrolls, vertically or sideways, from 360 to 1280 px on pages with long content", async ({ page }) => {
  test.setTimeout(120_000);
  await harness.current.signIn(page);
  for (const [width, height] of [[360, 640], [390, 844], [599, 800], [600, 800], [768, 700], [1023, 700], [1280, 600]] as const) {
    await page.setViewportSize({ width, height });
    for (const path of SHELL_PAGES) {
      await page.goto(`${harness.current.url}${path}`);
      await expect(shell(page)).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      const scroll = await documentScrolls(page);
      expect(scroll.vertical, `${path} at ${String(width)}x${String(height)}`).toBeLessThanOrEqual(0);
      expect(scroll.horizontal, `${path} at ${String(width)}x${String(height)}`).toBeLessThanOrEqual(0);
      expect(scroll.scrolledTo, `${path} at ${String(width)}x${String(height)}`).toBe(0);
      // The shell is exactly the window; the content is what scrolls.
      const box = await shell(page).boundingBox();
      expect(box?.height).toBe(height);
      expect(box?.width).toBe(width);
    }
  }
  // The pages really are longer than the window, so the proof is not about short pages.
  await page.setViewportSize({ width: 390, height: 640 });
  await page.goto(`${harness.current.url}/activity`);
  await expect(page.getByRole("heading", { name: "Pending drafts" })).toBeVisible();
  expect(await page.locator("#content").evaluate((node) => node.scrollHeight - node.clientHeight)).toBeGreaterThan(300);
});

test("UXP-09 the section header and the save bar stay in view while only the content between them scrolls", async ({ page }) => {
  await harness.current.signIn(page);
  for (const size of [{ width: 1280, height: 480 }, { width: 390, height: 520 }]) {
    await page.setViewportSize(size);
    await page.goto(`${harness.current.url}/s/monitor`);
    await expect(page.getByRole("heading", { level: 1, name: "Monitor profiles" })).toBeVisible();
    await page.locator("#content textarea").first().fill("changed so that the save bar shows");
    const bar = page.locator("[data-save-bar]");
    await expect(bar).toBeVisible();
    const header = page.getByRole("banner");
    const content = page.locator("#content");
    const scrolledBefore = await content.evaluate((node) => node.scrollTop);
    const before = { header: await header.boundingBox(), bar: await bar.boundingBox(), card: await page.locator("#content section").first().boundingBox() };
    await content.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    expect(await content.evaluate((node) => node.scrollTop)).toBeGreaterThan(100);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    const after = { header: await header.boundingBox(), bar: await bar.boundingBox() };
    expect(after.header, `header at ${String(size.width)}`).toEqual(before.header);
    expect(after.bar, `bar at ${String(size.width)}`).toEqual(before.bar);
    // The bar is on the screen: its bottom edge is at the window's bottom.
    expect(Math.round((after.bar?.y ?? 0) + (after.bar?.height ?? 0))).toBe(size.height);
    await expect(bar).toBeInViewport({ ratio: 1 });
    // And what scrolled is the content: its first card moved up by exactly the distance scrolled.
    const firstCard = await page.locator("#content section").first().boundingBox();
    const moved = (await content.evaluate((node) => node.scrollTop)) - scrolledBefore;
    expect((before.card?.y ?? 0) - (firstCard?.y ?? 0)).toBeCloseTo(moved, 0);
  }
});

/** Presses a key and waits until the scroller moved (the keyboard scrolls a focused region). */
async function scrolledBy(region: Locator, key: string, page: Page): Promise<number> {
  const before = await region.evaluate((node) => node.scrollTop);
  await page.keyboard.press(key);
  await expect.poll(() => region.evaluate((node) => node.scrollTop)).toBeGreaterThan(before);
  return region.evaluate((node) => node.scrollTop);
}

test("UXP-10 a long table on Status or Drafts and audit scrolls inside its own region with a sticky header row, and the region is named and reachable and scrollable by keyboard", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await page.goto(`${harness.current.url}/activity`);
  for (const title of ["Pending drafts", "Send audit", "Monitor audit"]) {
    const region = page.getByRole("region", { name: `${title} table` });
    await expect(region).toBeVisible();
    const sized = await region.evaluate((node) => ({ inner: node.clientHeight, all: node.scrollHeight, tab: node.getAttribute("tabindex"), rem: parseFloat(getComputedStyle(document.documentElement).fontSize) }));
    expect(sized.all, `${title} has more rows than fit`).toBeGreaterThan(sized.inner + 100);
    expect(sized.inner, `${title} is at most 24 rem`).toBeLessThanOrEqual(24 * sized.rem + 1);
    expect(sized.tab).toBe("0");
    // The table is the content of the region, with its own name.
    await expect(region.getByRole("table", { name: `${title} table` })).toHaveCount(1);
    expect(await region.locator("tbody tr").count()).toBe(45);
  }
  const region = page.getByRole("region", { name: "Send audit table" });
  await region.scrollIntoViewIfNeeded();
  // Keyboard: Tab reaches the region (it is focusable, so axe's rule holds), and the arrow keys scroll it.
  await region.focus();
  await expect(region).toBeFocused();
  const pageTop = await page.locator("#content").evaluate((node) => node.scrollTop);
  await expect(region).toHaveCSS("outline-style", "solid");
  const scrolled = await scrolledBy(region, "ArrowDown", page);
  expect(scrolled).toBeGreaterThan(0);
  await scrolledBy(region, "PageDown", page);
  // The header row stays at the top of the box while rows go by.
  const headBox = await region.locator("thead th").first().boundingBox();
  const regionBox = await region.boundingBox();
  expect(Math.abs((headBox?.y ?? -999) - ((regionBox?.y ?? 0) + 1))).toBeLessThanOrEqual(3);
  const rowBox = await region.locator("tbody tr").first().boundingBox();
  expect((rowBox?.y ?? 0) + (rowBox?.height ?? 0)).toBeLessThan((headBox?.y ?? 0) + 1);
  expect(await region.locator("thead th").first().evaluate((node) => getComputedStyle(node).position)).toBe("sticky");
  // The page itself did not move for any of that.
  expect(await page.locator("#content").evaluate((node) => node.scrollTop)).toBe(pageTop);

  // Status: the monitor replies are one row per profile and result, which is more than 24 rem holds.
  await page.goto(`${harness.current.url}/status`);
  const replies = page.getByRole("region", { name: "Monitor replies" });
  await expect(replies).toBeVisible();
  const facts = await replies.evaluate((node) => ({ inner: node.clientHeight, all: node.scrollHeight, tab: node.getAttribute("tabindex") }));
  expect(facts.all).toBeGreaterThan(facts.inner + 20);
  expect(facts.tab).toBe("0");
  await replies.focus();
  await scrolledBy(replies, "ArrowDown", page);
  expect(await replies.locator("thead th").first().evaluate((node) => getComputedStyle(node).position)).toBe("sticky");
  // A table that is only too wide is a region too: the webhook endpoints (one row, seven columns) scroll sideways.
  const endpoints = page.getByRole("region", { name: "Webhook endpoints" });
  await expect(endpoints).toBeVisible();
  expect(await endpoints.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeGreaterThan(20);
  await expect(endpoints).toHaveAttribute("tabindex", "0");
});

test("UXP-11 a records or list field keeps its heading and its add button in view while its items scroll in their own region", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await page.goto(`${harness.current.url}/s/monitor`);
  const heading = page.getByRole("heading", { level: 2, name: "Profiles" });
  await expect(heading).toBeVisible();
  const add = page.getByRole("button", { name: "Add record to Profiles" });
  const region = page.getByRole("region", { name: "Profiles, items" });
  await expect(region).toBeVisible();
  const facts = await region.evaluate((node) => ({ inner: node.clientHeight, all: node.scrollHeight, rem: parseFloat(getComputedStyle(document.documentElement).fontSize) }));
  expect(facts.all).toBeGreaterThan(facts.inner * 2);
  expect(facts.inner).toBeLessThanOrEqual(24 * facts.rem + 1);
  const before = { heading: await heading.boundingBox(), add: await add.boundingBox(), page: await page.locator("#content").evaluate((node) => node.scrollTop) };
  await region.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  expect(await region.evaluate((node) => node.scrollTop)).toBeGreaterThan(200);
  expect(await heading.boundingBox()).toEqual(before.heading);
  expect(await add.boundingBox()).toEqual(before.add);
  expect(await page.locator("#content").evaluate((node) => node.scrollTop)).toBe(before.page);
  await expect(heading).toBeInViewport();
  await expect(add).toBeInViewport();
  // The last profile is what the region now shows.
  await expect(region.locator("textarea").last()).toBeInViewport();
  await expect(region.getByRole("heading", { name: "profile-01" })).not.toBeInViewport();

  // A list field: add items until they pass 24 rem, the same holds.
  await page.goto(`${harness.current.url}/s/send_policy`);
  const listAdd = field(page, "allow").getByRole("button", { name: "Add item" });
  for (let index = 0; index < 14; index += 1) await listAdd.click();
  const list = page.getByRole("region", { name: "Allow, items" });
  await expect(list).toBeVisible();
  const listFacts = await list.evaluate((node) => ({ inner: node.clientHeight, all: node.scrollHeight }));
  expect(listFacts.all).toBeGreaterThan(listFacts.inner + 50);
  const listHead = await field(page, "allow").getByText("Allow", { exact: true }).first().boundingBox();
  const addBox = await listAdd.boundingBox();
  await list.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  expect(await field(page, "allow").getByText("Allow", { exact: true }).first().boundingBox()).toEqual(listHead);
  expect(await listAdd.boundingBox()).toEqual(addBox);
  await expect(listAdd).toBeInViewport();
});

test("UXP-12 a scroll region shows a shadow on each edge that cuts content, and none where nothing is cut", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await harness.current.signIn(page);
  await page.goto(`${harness.current.url}/activity`);
  const region = page.getByRole("region", { name: "Send audit table" });
  await region.scrollIntoViewIfNeeded();
  const scroller = region;
  const shell0 = region.locator("xpath=..");
  const top = shell0.locator('[data-edge="top"]');
  const bottom = shell0.locator('[data-edge="bottom"]');
  // At the start only the bottom is cut.
  await expect(bottom).toBeVisible();
  await expect(top).toHaveCount(0);
  await expect(scroller).toHaveAttribute("data-bottom", "true");
  await expect(scroller).toHaveAttribute("data-top", "false");
  const look = await bottom.evaluate((node) => {
    const css = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return { image: css.backgroundImage, height: box.height, width: box.width };
  });
  expect(look.image).toContain("gradient");
  expect(look.height).toBeGreaterThan(4);
  expect(look.width).toBeGreaterThan(200);
  // In the middle both edges are cut.
  await scroller.evaluate((node) => {
    node.scrollTop = 150;
  });
  await expect(top).toBeVisible();
  await expect(bottom).toBeVisible();
  // At the end only the top is cut.
  await scroller.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await expect(top).toBeVisible();
  await expect(bottom).toHaveCount(0);
  // Back at the start the shadow follows.
  await scroller.evaluate((node) => {
    node.scrollTop = 0;
  });
  await expect(top).toHaveCount(0);
  await expect(bottom).toBeVisible();
  // A list that fits has no region and no shadow: the Allow list of the fixture has two items.
  await page.goto(`${harness.current.url}/s/send_policy`);
  await expect(page.getByRole("textbox", { name: "Allow, item 1" })).toBeVisible();
  await expect(page.locator('[data-edge]')).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Allow, items" })).toHaveCount(0);
});

test("UXP-13 opening another section returns the content to the top and keeps the menu's scroll position", async ({ page }) => {
  // A short window, so that the menu itself has more entries than it can show.
  await page.setViewportSize({ width: 1280, height: 520 });
  await harness.current.signIn(page);
  await page.goto(`${harness.current.url}/activity`);
  await expect(page.getByRole("heading", { name: "Pending drafts" })).toBeVisible();
  const menu = menuNav(page).locator("[data-menu-scroll]");
  const room = await menu.evaluate((node) => node.scrollHeight - node.clientHeight);
  expect(room, "the menu scrolls in this window").toBeGreaterThan(200);
  const target = menuNav(page).getByRole("link", { name: /^Monitor profiles/ });
  await target.evaluate((node) => {
    node.scrollIntoView({ block: "center" });
  });
  const kept = await menu.evaluate((node) => node.scrollTop);
  expect(kept).toBeGreaterThan(50);
  const content = page.locator("#content");
  await content.evaluate((node) => {
    node.scrollTop = 600;
  });
  expect(await content.evaluate((node) => node.scrollTop)).toBeGreaterThan(400);
  await target.click();
  await expect(page.getByRole("heading", { level: 1, name: "Monitor profiles" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Profiles, items" })).toBeVisible();
  expect(await content.evaluate((node) => node.scrollTop)).toBe(0);
  expect(await menu.evaluate((node) => node.scrollTop)).toBe(kept);
  // And the other way: a scrolled section, then a section list entry elsewhere in the menu.
  await content.evaluate((node) => {
    node.scrollTop = 500;
  });
  const back = menuNav(page).getByRole("link", { name: /^Drafts and audit/ });
  await back.evaluate((node) => {
    node.scrollIntoView({ block: "center" });
  });
  const keptAgain = await menu.evaluate((node) => node.scrollTop);
  await back.click();
  await expect(page.getByRole("heading", { level: 1, name: "Drafts and audit" })).toBeVisible();
  expect(await content.evaluate((node) => node.scrollTop)).toBe(0);
  expect(await menu.evaluate((node) => node.scrollTop)).toBe(keptAgain);
});

test("UXP-34 from 360 to 599 px the open section fills the width under a header with Open menu and the menu is only the drawer, and the first route shows a page, not the menu", async ({ page }) => {
  await harness.current.signIn(page);
  for (const width of [360, 390, 480, 599]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`${harness.current.url}/`);
    // The first route shows a page under the header, not the menu.
    await expect(page.getByRole("banner").getByRole("heading", { level: 1 })).toBeVisible();
    await page.goto(`${harness.current.url}/status`);
    await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Status" })).toBeVisible();
    const main = await page.getByRole("main").boundingBox();
    const header = await page.getByRole("banner").boundingBox();
    expect(main?.x).toBe(0);
    expect(main?.width).toBe(width);
    expect(header?.x).toBe(0);
    expect(header?.width).toBe(width);
    expect((header?.y ?? 99) + (header?.height ?? 0)).toBeLessThanOrEqual((main?.y ?? 0) + 1);
    await expect(page.getByRole("banner").getByRole("button", { name: "Open menu" })).toBeVisible();
    await expect(panel(page)).toHaveCount(0);
    await expect(menuNav(page)).toHaveCount(0);
    // A form section fills the width too.
    await page.goto(`${harness.current.url}/s/channel`);
    await expect(page.locator("form [data-field]").first()).toBeVisible();
    const card = await page.locator("#content form section").first().boundingBox();
    expect(card?.x).toBe(16);
    expect(card?.width).toBe(width - 32);
  }
  // The menu exists only as the drawer.
  await page.setViewportSize({ width: 390, height: 800 });
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(page.getByRole("dialog", { name: "Sections menu" }).getByRole("navigation", { name: "Sections" })).toBeVisible();
});

test("UXP-42 from 600 to 1023 px the icon rail and the section sit side by side and the section uses all the width left of the rail", async ({ page }) => {
  await harness.current.signIn(page);
  await page.evaluate(() => {
    localStorage.clear();
  });
  for (const width of [600, 768, 1023]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`${harness.current.url}/s/channel`);
    await expect(page.locator("form [data-field]").first()).toBeVisible();
    await expect(shell(page)).toHaveAttribute("data-menu", "collapsed");
    const rail = await panel(page).boundingBox();
    const main = await page.getByRole("main").boundingBox();
    const header = await page.getByRole("banner").boundingBox();
    expect(rail?.x).toBe(0);
    expect(rail?.width).toBe(72);
    // Side by side: the section starts where the rail ends and ends at the window's edge.
    expect(main?.x, `${String(width)} px`).toBe(72);
    expect((main?.x ?? 0) + (main?.width ?? 0), `${String(width)} px`).toBe(width);
    expect(header?.x).toBe(72);
    expect(header?.width).toBe(width - 72);
    expect(rail?.y).toBe(0);
    expect(main?.y).toBeGreaterThanOrEqual((header?.y ?? 0) + (header?.height ?? 0) - 1);
    // A content area that is narrower than the 48 rem cap takes the whole pane minus its gutters.
    const card = await page.locator("#content form section").first().boundingBox();
    const room = width - 72;
    const gutters = await page.locator("#content > div").evaluate((node) => parseFloat(getComputedStyle(node).paddingLeft) + parseFloat(getComputedStyle(node).paddingRight));
    expect(gutters).toBeGreaterThan(0);
    if (room <= 720 + gutters) expect(card?.width, `${String(width)} px`).toBeCloseTo(room - gutters, 0);
    else expect(card?.width, `${String(width)} px`).toBeGreaterThan(600);
    const scroll = await documentScrolls(page);
    expect(scroll.horizontal).toBeLessThanOrEqual(0);
  }
});

/** A CDP session of the page, for touch input. */
async function touchOf(page: Page): Promise<CDPSession> {
  return page.context().newCDPSession(page);
}

async function swipe(cdp: CDPSession, from: { x: number; y: number }, dx: number): Promise<void> {
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
  const steps = 6;
  for (let step = 1; step <= steps; step += 1) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: from.x + (dx * step) / steps, y: from.y }] });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

test("UXP-44 Open menu slides a drawer in from the left, at most 320 px and 85% of the width, over a dimmed overlay, with groups, the filter, entries at least 48 px tall, focus kept inside, named Sections menu", async ({ browser }) => {
  // Three phones, and Tab through the whole drawer on each: the focus walk is what takes the time.
  test.setTimeout(120_000);
  for (const size of [{ width: 390, height: 844 }, { width: 360, height: 640 }, { width: 599, height: 800 }]) {
    const { context, page } = await phonePage(browser, size);
    try {
      await menuReady(page).catch(() => undefined);
      await page.evaluate(() => {
        const lefts: number[] = [];
        (window as unknown as { __lefts: number[] }).__lefts = lefts;
        const tick = (): void => {
          const node = document.querySelector("[role=dialog]");
          if (node) lefts.push(node.getBoundingClientRect().left);
          requestAnimationFrame(tick);
        };
        tick();
      });
      await page.getByRole("button", { name: "Open menu" }).click();
      const drawer = page.getByRole("dialog", { name: "Sections menu" });
      await expect(drawer).toBeVisible();
      // It slid in from the left edge: it was off-screen at first and rests at x = 0.
      await expect.poll(async () => (await drawer.boundingBox())?.x).toBe(0);
      const lefts = await page.evaluate(() => (window as unknown as { __lefts: number[] }).__lefts);
      expect(Math.min(...lefts), "the drawer starts off-screen").toBeLessThan(-50);
      const box = await drawer.boundingBox();
      const limit = Math.min(320, size.width * 0.85);
      expect(box?.width, `${String(size.width)} px`).toBeLessThanOrEqual(320);
      expect(box?.width).toBeLessThanOrEqual(size.width * 0.85 + 0.5);
      expect(box?.width).toBeGreaterThan(limit - 1);
      // Over a dimmed overlay that covers the window.
      const overlay = page.locator("[data-sheet-overlay]");
      const dim = await overlay.evaluate((node) => {
        const css = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return { color: css.backgroundColor, rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height } };
      });
      expect(dim.rect).toEqual({ x: 0, y: 0, w: size.width, h: size.height });
      const alpha = /rgba?\([^)]*?,\s*([\d.]+)\)/.exec(dim.color)?.[1] ?? /\/\s*([\d.]+)\)/.exec(dim.color)?.[1];
      expect(Number(alpha), dim.color).toBeGreaterThan(0.3);
      // The groups, the filter and every entry at least 48 px tall.
      const nav = drawer.getByRole("navigation", { name: "Sections" });
      await expect(nav.getByRole("heading", { level: 2 })).toHaveText(["Overview", "Messaging", "Safety and data", "Preferences", "More"]);
      await expect(nav.getByRole("searchbox", { name: "Filter sections" })).toBeVisible();
      const heights = await nav.locator("a").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height));
      expect(heights.length).toBeGreaterThan(10);
      for (const height of heights) expect(height).toBeGreaterThanOrEqual(48);
      // Focus is inside the drawer, and Tab and Shift+Tab never leave it.
      expect(await page.evaluate(() => document.activeElement?.closest("[role=dialog]") !== null)).toBe(true);
      for (let step = 0; step < 24; step += 1) {
        await page.keyboard.press("Tab");
        expect(await page.evaluate(() => document.activeElement?.closest("[role=dialog]") !== null), `Tab ${String(step)}`).toBe(true);
      }
      for (let step = 0; step < 24; step += 1) {
        await page.keyboard.press("Shift+Tab");
        expect(await page.evaluate(() => document.activeElement?.closest("[role=dialog]") !== null), `Shift+Tab ${String(step)}`).toBe(true);
      }
    } finally {
      await context.close();
    }
  }
});

test("UXP-45 choosing an entry, Escape, Close menu and a tap on the overlay each close the drawer and return focus to Open menu, and an entry also opens its section", async ({ browser }) => {
  const { context, page } = await phonePage(browser);
  try {
    const start = page.url();
    const open = page.getByRole("button", { name: "Open menu" });
    const drawer = page.getByRole("dialog", { name: "Sections menu" });
    const closers: [string, () => Promise<void>][] = [
      ["Escape", () => page.keyboard.press("Escape")],
      ["Close menu", () => drawer.getByRole("button", { name: "Close menu" }).click()],
      ["overlay", () => page.mouse.click(PHONE.width - 12, PHONE.height / 2)],
    ];
    for (const [name, close] of closers) {
      await open.click();
      await expect(drawer).toBeVisible();
      await close();
      await expect(drawer, name).toHaveCount(0);
      await expect(open, `focus after ${name}`).toBeFocused();
      await expect(page).toHaveURL(start);
    }
    // An entry: the section opens, the drawer closes and focus goes back to the button.
    await open.click();
    await drawer.getByRole("link", { name: /^Privacy/ }).click();
    await expect(drawer).toHaveCount(0);
    await expect(page).toHaveURL(/\/s\/privacy$/);
    await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Privacy" })).toBeVisible();
    await expect(open).toBeFocused();
    // The entry is also reachable and chosen by keyboard.
    await open.click();
    await drawer.getByRole("link", { name: /^Time zone/ }).focus();
    await page.keyboard.press("Enter");
    await expect(drawer).toHaveCount(0);
    await expect(page).toHaveURL(/\/s\/timezone$/);
    await expect(open).toBeFocused();
  } finally {
    await context.close();
  }
});

test("UXP-46 while the drawer is open the page behind it does not scroll, cannot take focus and cannot be tapped", async ({ browser }) => {
  const { context, page } = await phonePage(browser);
  try {
    await page.goto(`${harness.current.url}/activity`);
    await expect(page.getByRole("heading", { name: "Pending drafts" })).toBeVisible();
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("dialog", { name: "Sections menu" });
    await expect(drawer).toBeVisible();
    // Scrolling over the overlay does not move the content.
    const content = page.locator("#content");
    await page.mouse.move(PHONE.width - 20, 400);
    await page.mouse.wheel(0, 800);
    await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    expect(await content.evaluate((node) => node.scrollTop)).toBe(0);
    // The page is hidden from assistive technology (the main landmark is out of the tree) ...
    await expect(page.getByRole("main")).toHaveCount(0);
    // ... the dialog primitive has locked the page's scroll ...
    await expect(page.locator("body")).toHaveAttribute("data-scroll-locked", /.+/);
    // ... a program that tries to focus a control behind the drawer gets the focus pulled back ...
    await content.locator("button").first().evaluate((node) => {
      (node as HTMLElement).focus();
    });
    expect(await page.evaluate(() => document.activeElement?.closest("[role=dialog]") !== null)).toBe(true);
    // ... and a tap lands on the overlay, never on a control behind it.
    const reached = await page.evaluate(() => {
      const behind = Array.from(document.querySelectorAll<HTMLElement>("#content button, #content a, #content [tabindex='0'], header button"));
      return behind
        .filter((node) => !node.closest("[role=dialog]"))
        .map((node) => {
          const rect = node.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0) return "skipped";
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
          return hit !== null && (node === hit || node.contains(hit)) ? `reached ${node.tagName} ${node.textContent}` : "blocked";
        });
    });
    expect(reached.length).toBeGreaterThan(3);
    expect(reached.filter((state) => state.startsWith("reached"))).toEqual([]);
    // After it closes the page works again.
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.locator("body")).not.toHaveAttribute("data-scroll-locked", /.+/);
    await content.evaluate((node) => {
      node.scrollTop = 300;
    });
    expect(await content.evaluate((node) => node.scrollTop)).toBe(300);
  } finally {
    await context.close();
  }
});

test("UXP-47 a swipe of the drawer to the left past a third of its width closes it, a shorter one does not", async ({ browser }) => {
  const { context, page } = await phonePage(browser);
  try {
    const cdp = await touchOf(page);
    const drawer = page.getByRole("dialog", { name: "Sections menu" });
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect.poll(async () => (await drawer.boundingBox())?.x).toBe(0);
    const width = (await drawer.boundingBox())?.width ?? 0;
    expect(width).toBe(320);
    // A short swipe (a quarter of the width): the drawer comes back and stays open.
    await swipe(cdp, { x: 260, y: 700 }, -Math.round(width * 0.25));
    await expect.poll(async () => (await drawer.boundingBox())?.x).toBe(0);
    await expect(drawer).toBeVisible();
    // A swipe past a third (about 53 %): the drawer closes.
    await swipe(cdp, { x: 260, y: 700 }, -Math.round(width * 0.53));
    await expect(drawer).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
    // A mostly vertical drag is the menu scrolling, not a swipe.
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect.poll(async () => (await drawer.boundingBox())?.x).toBe(0);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: 260, y: 600 }] });
    for (let step = 1; step <= 6; step += 1) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: 260 - step * 25, y: 600 - step * 30 }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(drawer).toBeVisible();
  } finally {
    await context.close();
  }
});

test("UXP-48 with unsaved edits, choosing another section from the drawer asks before leaving, as on wide screens", async ({ browser }) => {
  const { context, page } = await phonePage(browser);
  try {
    await page.goto(`${harness.current.url}/s/send_policy`);
    await page.getByRole("textbox", { name: "Allow, item 1" }).fill("Draft that must not be lost");
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("dialog", { name: "Sections menu" });
    await drawer.getByRole("link", { name: /^Webhooks/ }).click();
    const ask = page.getByRole("alertdialog");
    await expect(ask).toContainText("Leave without saving?");
    await expect(page).toHaveURL(/\/s\/send_policy$/);
    // Stay: the section and the draft are as they were.
    await ask.getByRole("button", { name: "Stay" }).click();
    await expect(ask).toHaveCount(0);
    await expect(page).toHaveURL(/\/s\/send_policy$/);
    await expect(page.getByRole("textbox", { name: "Allow, item 1" })).toHaveValue("Draft that must not be lost");
    // Leave: the other section opens.
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("dialog", { name: "Sections menu" }).getByRole("link", { name: /^Webhooks/ }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Discard and leave" }).click();
    await expect(page).toHaveURL(/\/s\/webhooks$/);
    await expect(page.getByRole("banner").getByRole("heading", { level: 1, name: "Webhooks" })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // Without edits there is no question.
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("dialog", { name: "Sections menu" }).getByRole("link", { name: /^Privacy/ }).click();
    await expect(page).toHaveURL(/\/s\/privacy$/);
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
  } finally {
    await context.close();
  }
});
