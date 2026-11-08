import AxeBuilder from "@axe-core/playwright";
import { createHash } from "node:crypto";
import { chmodSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { expect, harnessForFile, SERVER_DIR, test, withHarness } from "./support/fixtures";
import type { BridgeMode } from "./support/fakeBridge";
import { confirm, field, json, openSection, save, saveButton } from "./support/sections";

// Parity with 0.6.0 for EXT-01..03, OPS-01..03 and UX-01..06 of the interface-grafica spec.
// One test per requirement, titled with its ID.

const harness = harnessForFile();
const FRONTEND = resolve(SERVER_DIR, "frontend");

/** A hash of every file the interface serves: if the shell changed, this changes. */
function shellFingerprint(): string {
  const hash = createHash("sha256");
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else hash.update(path).update(readFileSync(path));
    }
  };
  walk(join(SERVER_DIR, "ui", "web"));
  return hash.digest("hex");
}

/** A section module in the form a developer would write it: it declares fields, a validator and where it is stored. */
const EXTRA_SECTION = `"""A section added for the end-to-end tests. Dropping this file next to the others is all it takes."""
import os

import localconfig
from ui.config.fields import Field, Problem
from ui.config.registry import JsonFile, Section


def _problems(values):
    count = values.get("count")
    return [Problem("count", "must not be 13")] if count == 13 else []


SECTION = Section(
    id="e2e_extra", title="Added at runtime", order=500, icon="radio",
    storage=JsonFile(lambda: os.path.join(localconfig.config_dir(), "e2e_extra.json")),
    fields=(
        Field("greeting", "Greeting", "text", default="hello", help="A text the module declared."),
        Field("count", "Count", "int", default=3, bounds=(1, 99)),
    ),
    validator=_problems,
    description="A section that was not in the menu when the interface started.",
)
`;

const FACTS_SECTION = `"""A read-only section for the end-to-end tests: facts that are shown and never edited."""
from ui.config.fields import Field
from ui.config.registry import ReadOnly, Section


def _facts():
    return {"flavour": "vanilla", "scoops": "2"}


SECTION = Section(
    id="e2e_facts", title="Facts of the suite", order=501, icon="no-such-icon", storage=ReadOnly(),
    fields=(Field("flavour", "Flavour", "text"), Field("scoops", "Scoops", "text")),
    facts=_facts, description="Declared by the test suite.",
)
`;

test("EXT-01 a section module dropped in at runtime shows up in the menu and works, without touching the shell", async ({ page }) => {
  await withHarness({ extraSections: true }, async (fresh) => {
    const nav = page.getByRole("navigation", { name: "Sections" });
    await fresh.signIn(page);
    await expect(nav.getByRole("link", { name: /^Added at runtime/ })).toHaveCount(0);
    const before = shellFingerprint();

    // A developer adds one module; the interface is started again, as the bridge does it.
    writeFileSync(join(fresh.extraDir ?? "", "e2e_extra.py"), EXTRA_SECTION);
    await fresh.restart();
    await fresh.signIn(page);
    await nav.getByRole("link", { name: /^Added at runtime/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Added at runtime" })).toBeVisible();
    await expect(page.getByLabel("Greeting")).toHaveValue("hello");
    await expect(page.getByLabel("Count")).toHaveValue("3");

    // Its own validator and storage are used, through the same form as every other section.
    await field(page, "count").getByRole("textbox").fill("13");
    await saveButton(page).click();
    await expect(field(page, "count")).toContainText("must not be 13");
    expect(fresh.read("e2e_extra.json")).toBeUndefined();
    await field(page, "count").getByRole("textbox").fill("7");
    await page.getByLabel("Greeting").fill("hi there");
    await save(page);
    expect(json(fresh, "e2e_extra.json")).toEqual({ greeting: "hi there", count: 7 });
    expect(shellFingerprint()).toBe(before);
  });
});

test("EXT-02 presentation, state, services and file access live in separate modules and the rules come from the project", () => {
  const src = (path: string): string => readFileSync(join(FRONTEND, "src", path), "utf8");
  const listing = (path: string): string[] =>
    readdirSync(join(FRONTEND, "src", path), { recursive: true, encoding: "utf8" }).filter((name) => /\.tsx?$/.test(name));
  // Presentation, state, services and the HTTP client are separate places.
  for (const dir of ["components", "features", "stores", "services", "lib"]) expect(listing(dir).length, dir).toBeGreaterThan(0);
  // Only the client talks HTTP; the plain components know neither the client nor the services.
  const talkers = [...listing("."), ].filter((name) => /from "axios"/.test(src(name)));
  expect(talkers).toEqual([join("lib", "api.ts")]);
  for (const name of listing("components")) {
    expect(src(join("components", name)), name).not.toMatch(/@\/services|@\/lib\/api|from "axios"/);
  }
  // The server side: sections import the project's own rules, and only the store opens a section file.
  const server = (path: string): string => readFileSync(join(SERVER_DIR, "ui", path), "utf8");
  expect(server("sections/send_policy.py")).toMatch(/^import send_policy$/m);
  expect(server("sections/monitor.py")).toMatch(/^import monitor$/m);
  expect(server("sections/channel.py")).toMatch(/^import channel$/m);
  for (const dir of ["sections", "api"]) {
    for (const name of readdirSync(join(SERVER_DIR, "ui", dir)).filter((entry) => entry.endsWith(".py"))) {
      // The two read-only readers (the database facts and the audit logs) open files to read; nothing writes.
      const text = server(join(dir, name));
      expect(text, `${dir}/${name}`).not.toMatch(/os\.replace|json\.dump\(|open\([^)]*['"]w/);
    }
  }
});

test("EXT-03 the suite registers a test section and the interface shows it as it is, without a change to the shell", async ({ page }) => {
  await withHarness({ extraSections: true }, async (fresh) => {
    writeFileSync(join(fresh.extraDir ?? "", "e2e_facts.py"), FACTS_SECTION);
    await fresh.restart();
    const before = shellFingerprint();
    await fresh.signIn(page);
    const nav = page.getByRole("navigation", { name: "Sections" });
    const entry = nav.getByRole("link", { name: /^Facts of the suite/ });
    await expect(entry).toBeVisible();
    // It sits where its order puts it: after the pages and sections that come before 501.
    const names = await nav.getByRole("link").allInnerTexts();
    expect(names.findIndex((text) => text.startsWith("Facts of the suite"))).toBe(names.length - 1);
    await entry.click();
    await expect(page.getByRole("heading", { level: 1, name: "Facts of the suite" })).toBeVisible();
    await expect(page.getByLabel("Flavour")).toHaveValue("vanilla");
    await expect(page.getByLabel("Flavour")).toHaveAttribute("readonly", "");
    await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
    expect(shellFingerprint()).toBe(before);
  });
});

const DAY = "2026-01-02";

test("OPS-01 the status page shows the bridge's state, uptime and events, and says so when the bridge does not answer", async ({ page }) => {
  const { bridge, url } = harness.current;
  await harness.current.signIn(page);
  bridge.mode = "connected";
  await page.goto(`${url}/status`);
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByText("Paired with a phone")).toBeVisible();
  await expect(page.getByText("2 h 5 min")).toBeVisible();
  await expect(page.getByText("0.0.0-test")).toBeVisible();
  await expect(page.getByText("Jan 2, 2026, 05:00 UTC")).toBeVisible();
  await expect(page.getByRole("region", { name: "Webhook endpoints" })).toContainText("hook-a");

  bridge.mode = "disconnected";
  await page.goto(`${url}/status`);
  await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
  await expect(page.getByText("Not paired")).toBeVisible();

  const reasons: [BridgeMode, string][] = [
    ["down", "The bridge is not responding."],
    ["unauthorized", "The bridge refused the interface's access token. Restart the bridge so they use the same one."],
    ["unsupported", "This bridge does not offer that function. Update the bridge."],
    ["unexpected", "The bridge gave an answer that could not be understood."],
  ];
  for (const [mode, sentence] of reasons) {
    bridge.mode = mode;
    await page.goto(`${url}/status`);
    await expect(page.getByText("Bridge status unavailable")).toBeVisible();
    await expect(page.getByText(sentence).first()).toBeVisible();
    await expect(page.getByText("Connected", { exact: true })).toHaveCount(0);
  }
  bridge.mode = "connected";
});

test("OPS-02 counters, pending drafts and both audit logs show without message text and without any way to act on them", async ({ page }) => {
  await withHarness({}, async (fresh) => {
    const text = "SECRET-MESSAGE-TEXT-DO-NOT-SHOW";
    const future = "2099-01-01T00:00:00+00:00";
    fresh.seedActivity({
      drafts: [
        { id: "d_old", profile: "assistant", chat_jid: "120363000000000001@g.us", message_id: "m1", sender: "15550100001", text, created_at: `${DAY}T01:00:00+00:00`, expires_at: future },
        { id: "d_new", profile: "assistant", chat_jid: "120363000000000001@g.us", message_id: "m2", sender: "15550100001", text, created_at: `${DAY}T09:00:00+00:00`, expires_at: future },
      ],
      send: [
        { ts: `${DAY}T01:00:00+00:00`, kind: "text", chat_jid: "a@s.whatsapp.net", chat_name: "Older chat", size: 11, result: "sent", rule: "allow", override: false, message_id: "s1", text },
        { ts: `${DAY}T09:00:00+00:00`, kind: "text", chat_jid: "b@s.whatsapp.net", chat_name: "Newer chat", size: 22, result: "blocked", rule: "deny", override: true, message_id: "s2", text },
      ],
      monitor: [
        { ts: `${DAY}T02:00:00+00:00`, profile: "oldprof", chat_jid: "c@g.us", sender: "x", message_id: "m1", triggers: ["mention"], mode: "draft", approval_used: false, result: "drafted", size: 5, text },
        { ts: `${DAY}T08:00:00+00:00`, profile: "newprof", chat_jid: "c@g.us", sender: "x", message_id: "m2", triggers: ["reply", "topic"], mode: "auto", approval_used: true, result: "sent", size: 6, excerpt: text },
      ],
    });
    const bodies: string[] = [];
    page.on("response", (response) => {
      if (new URL(response.url()).pathname.startsWith("/api/ops/")) void response.text().then((body) => bodies.push(body), () => undefined);
    });
    await fresh.signIn(page);
    await page.goto(`${fresh.url}/activity`);
    const first = async (region: string): Promise<string> =>
      (await page.getByRole("region", { name: region }).getByRole("row").nth(1).innerText()).replace(/\s+/g, " ");
    // The newest row comes first, in all three lists.
    await expect(page.getByRole("region", { name: "Pending drafts" })).toContainText("d_new");
    expect(await first("Pending drafts")).toContain("d_new");
    expect(await first("Send audit")).toContain("Newer chat");
    expect(await first("Monitor audit")).toContain("newprof");
    // The counters of the status page count the drafts too.
    await page.goto(`${fresh.url}/status`);
    const pending = page.getByRole("main").getByText("Pending drafts", { exact: true }).locator("xpath=..");
    await expect(pending).toContainText("2");
    // No text of a message anywhere: not on the pages, not in an answer.
    await page.goto(`${fresh.url}/activity`);
    await expect(page.getByRole("region", { name: "Monitor audit" })).toBeVisible();
    expect(await page.locator("body").innerText()).not.toContain(text);
    expect(bodies.length).toBeGreaterThan(2);
    for (const body of bodies) expect(body).not.toContain(text);
    // Nothing on the page sends, approves or discards, and the API has no such route.
    const content = page.getByRole("main");
    await expect(content.getByRole("button")).toHaveCount(0);
    await expect(content.getByRole("link")).toHaveCount(0);
    const session = (await (await page.request.get(`${fresh.url}/api/session`)).json()) as { csrf: string };
    for (const route of ["/api/ops/drafts", "/api/ops/audit/send", "/api/ops/status", "/api/send", "/api/drafts/d_new/approve", "/api/drafts/d_new"]) {
      for (const verb of ["post", "put", "delete"] as const) {
        const answer = await page.request[verb](`${fresh.url}${route}`, { headers: { "X-CSRF-Token": session.csrf }, data: {} });
        expect(answer.ok(), `${verb} ${route}`).toBe(false);
      }
    }
    expect(JSON.parse(readFileSync(fresh.file("monitor_drafts.json"), "utf8"))).toHaveLength(2);
  });
});

test("OPS-03 the status page refreshes at a moderate interval while it is open and stops when the operator leaves", async ({ page }) => {
  const { url } = harness.current;
  const polls: { path: string; background: string | undefined }[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/ops/")) polls.push({ path, background: request.headers()["x-background"] });
  });
  await harness.current.signIn(page);
  await page.clock.install({ time: new Date(`${DAY}T03:00:00Z`) });
  const statusPolls = (): number => polls.filter((poll) => poll.path === "/api/ops/status").length;
  const settle = async (): Promise<void> => {
    await page.evaluate(() => fetch("/api/session", { headers: { "X-Background": "1" } }).then(() => undefined));
  };

  await page.goto(`${url}/status`);
  await expect.poll(statusPolls).toBeGreaterThanOrEqual(1);
  await settle();
  const opened = statusPolls();
  // Every five seconds, not faster: four seconds bring nothing, one more second brings one read.
  await page.clock.runFor(4000);
  await settle();
  expect(statusPolls()).toBe(opened);
  await page.clock.runFor(1000);
  await expect.poll(statusPolls).toBe(opened + 1);
  await page.clock.runFor(5000);
  await expect.poll(statusPolls).toBe(opened + 2);
  // The reads do not keep the session alive.
  expect(polls.every((poll) => poll.background === "1")).toBe(true);

  // A hidden tab does not poll.
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await settle();
  const hidden = statusPolls();
  await page.clock.runFor(30_000);
  await settle();
  expect(statusPolls()).toBe(hidden);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  });

  // Leaving the page ends the polling.
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: /^Account/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
  await settle();
  const left = polls.length;
  await page.clock.runFor(30_000);
  await settle();
  expect(polls.length).toBe(left);
});

const WIDTHS = [360, 768, 1280];
const PAGES = ["/status", "/activity", "/account", "/s/send_policy", "/s/monitor", "/s/webhooks", "/s/media"];

test("UX-01 the layout adapts to 360, 768 and 1280 px with the menu beside the page when wide and no horizontal scroll", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json", "monitor_profiles.json", "webhooks.json"] }, async (fresh) => {
    fresh.seedActivity({
      drafts: [{ id: "d_a_rather_long_identifier_for_a_draft", profile: "a-long-profile-name", chat_jid: "120363000000000001@g.us", sender: "15550100001", created_at: `${DAY}T01:00:00+00:00`, expires_at: "2099-01-01T00:00:00+00:00" }],
      send: [{ ts: `${DAY}T01:00:00+00:00`, kind: "text", chat_jid: "a-very-long-chat-identifier@s.whatsapp.net", chat_name: "A chat with a very long name indeed", size: 11, result: "sent", rule: "allow", override: false, message_id: "s1" }],
    });
    await fresh.signIn(page);
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 800 });
      for (const path of PAGES) {
        await page.goto(`${fresh.url}${path}`);
        await expect(page.getByTestId("shell")).toBeVisible();
        const nav = page.getByRole("navigation", { name: "Sections" });
        if (width >= 600) {
          await expect(nav).toBeVisible();
          await expect(page.getByRole("main")).toBeVisible();
        } else {
          // One pane: the section fills the screen; the menu is the drawer behind "Open menu".
          await expect(nav).toHaveCount(0);
          await expect(page.getByRole("main")).toBeVisible();
          await page.getByRole("button", { name: "Open menu" }).click();
          await expect(nav).toBeVisible();
          await nav.getByRole("link").first().click();
          await expect(page.getByRole("main")).toBeVisible();
          await expect(nav).toHaveCount(0);
          await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
          await page.goto(`${fresh.url}${path}`);
          await page.getByRole("button", { name: "Open menu" }).click();
          await page.getByRole("navigation", { name: "Sections" }).locator(`a[href="${path}"]`).click();
          await expect(page.getByRole("main")).toBeVisible();
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, `${path} at ${String(width)} px`).toBeLessThanOrEqual(0);
      }
    }
  });
});

test("UX-02 every control is reached and operated by keyboard with a visible focus, a name, and 4.5:1 text contrast", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json", "monitor_profiles.json"] }, async (fresh) => {
    await fresh.signIn(page);
    await page.setViewportSize({ width: 1280, height: 900 });
    for (const path of ["/s/send_policy", "/s/monitor", "/account"]) {
      await page.goto(`${fresh.url}${path}`);
      await expect(page.getByRole("main")).toBeVisible();
      await page.locator("body").click({ position: { x: 1, y: 1 } });
      const seen: string[] = [];
      const ringless: string[] = [];
      for (let step = 0; step < 160; step += 1) {
        await page.keyboard.press("Tab");
        const info = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (el === null || el === document.body) return null;
          const style = getComputedStyle(el);
          const ring = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0 || style.boxShadow !== "none";
          const inMain = el.closest("main") !== null;
          el.setAttribute("data-visited", "yes");
          return { inMain, ring, focusVisible: el.matches(":focus-visible"), label: `${el.tagName}:${el.getAttribute("aria-label") ?? el.textContent}`.slice(0, 60) };
        });
        if (info === null) continue;
        if (info.inMain) {
          seen.push(info.label);
          if (!info.ring || !info.focusVisible) ringless.push(info.label);
        }
        if (await page.evaluate(() => document.activeElement?.closest("main") === null && document.querySelectorAll("main [data-visited]").length > 0)) break;
      }
      expect(ringless, `${path}: focus must be visible`).toEqual([]);
      // Every enabled control that is in the tab order was reached. The unchecked radios of a group are not in it
      // (arrows move inside the group) and the hidden inputs of the kit's switches and radios are not controls.
      const missed = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("main :is(button, input, textarea, [role=radio], [role=switch], [role=combobox], [role=checkbox])")]
          .filter((node) => !(node as HTMLButtonElement).disabled && node.getAttribute("tabindex") !== "-1" && node.closest("[aria-hidden=true]") === null)
          .filter((node) => !node.hasAttribute("data-visited"))
          .map((node) => node.getAttribute("aria-label") ?? (node.textContent || node.tagName)),
      );
      expect(missed, `${path}: every control of the page is reached by Tab`).toEqual([]);
      expect(seen.length, `${path}: the page has controls`).toBeGreaterThan(3);
    }
    // Operate by keyboard: a switch with Space, a radio with the arrow keys, a button with Enter.
    await page.goto(`${fresh.url}/s/send_policy`);
    await page.getByRole("radio", { name: "allowlist" }).focus();
    await page.keyboard.press("ArrowUp", { delay: 60 });
    await expect(page.getByRole("radio", { name: "denylist" })).toBeChecked();
    await field(page, "allow").getByRole("button", { name: "Add item" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("textbox", { name: "Allow, item 3" })).toBeFocused();
    // Names: every control has one, and the text meets 4.5:1 in the light and the dark theme.
    // Colors ease over 150 ms when the theme changes; the check reads the settled ones.
    await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; }" });
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      const result = await new AxeBuilder({ page })
        .include("main")
        .withRules(["button-name", "label", "aria-input-field-name", "aria-toggle-field-name", "color-contrast", "aria-allowed-role", "aria-required-attr"])
        .analyze();
      expect(result.violations.map((violation) => `${scheme}: ${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(" | ")}`)).toEqual([]);
    }
  });
});

test("UX-03 loading, empty, error and unavailable states are each shown", async ({ page }) => {
  await withHarness({}, async (fresh) => {
    await fresh.signIn(page);
    // Loading: a skeleton, marked busy, while the section is on its way.
    let release: () => void = () => undefined;
    const gate = new Promise<void>((done) => {
      release = done;
    });
    await page.route("**/api/sections/privacy", async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto(`${fresh.url}/s/privacy`);
    await expect(page.locator('[aria-busy="true"][aria-label="Loading"]')).toBeVisible();
    release();
    await expect(page.getByRole("switch", { name: "Keep deleted messages" })).toBeVisible();
    await page.unroute("**/api/sections/privacy");

    // Empty: the file is not there, and the page says the defaults are in use.
    await page.goto(`${fresh.url}/s/send_policy`);
    await expect(page.getByText("Nothing is saved here yet. The defaults are in use.")).toBeVisible();
    await page.goto(`${fresh.url}/activity`);
    await expect(page.getByText("No drafts are waiting.")).toBeVisible();
    await expect(page.getByText("No sends are logged yet.")).toBeVisible();

    // Error: the section cannot be read; the page says so and the retry works once the server answers again.
    await page.route("**/api/sections/timezone", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ code: "error", message: "Down." }) }),
    );
    await page.goto(`${fresh.url}/s/timezone`);
    await expect(page.getByRole("alert").filter({ hasText: "This section could not be loaded." })).toBeVisible();
    await page.unroute("**/api/sections/timezone");
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("textbox", { name: "Time zone" })).toBeVisible();

    // Unavailable: the bridge does not answer.
    fresh.bridge.mode = "down";
    await page.goto(`${fresh.url}/status`);
    await expect(page.getByText("Bridge status unavailable")).toBeVisible();
    await expect(page.getByText("The bridge is not responding.").first()).toBeVisible();
    fresh.bridge.mode = "connected";
  });
});

const notifications = (page: Page): Locator => page.getByRole("region", { name: "Notifications" });

test("UX-04 each result shows beside the control, and a global event shows once as a notification", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json"] }, async (fresh) => {
    await fresh.signIn(page);
    await openSection(page, fresh, "send_policy");
    // Saved: one notification (the save bar goes away); a second save inside a minute does not repeat it.
    await field(page, "deny").getByRole("button", { name: "Add item" }).click();
    await page.getByRole("textbox", { name: "Deny, item 2" }).fill("First");
    await save(page);
    await expect(notifications(page).getByText("Changes saved.")).toHaveCount(1);
    await field(page, "deny").getByRole("button", { name: "Add item" }).click();
    await page.getByRole("textbox", { name: "Deny, item 3" }).fill("Second");
    await save(page);
    await expect(notifications(page).getByText("Changes saved.")).toHaveCount(1);

    // A write that fails: the message is beside the form and the global event is announced once.
    chmodSync(fresh.configDir, 0o500);
    try {
      await field(page, "deny").getByRole("button", { name: "Add item" }).click();
      await page.getByRole("textbox", { name: "Deny, item 4" }).fill("Third");
      await saveButton(page).click();
      await expect(page.getByRole("alert").filter({ hasText: "the file could not be written" })).toBeVisible();
      await expect(notifications(page).getByText("The changes could not be saved.")).toHaveCount(1);
      await saveButton(page).click();
      await expect(notifications(page).getByText("The changes could not be saved.")).toHaveCount(1);
    } finally {
      chmodSync(fresh.configDir, 0o700);
    }
    expect((json(fresh, "send_policy.json") as { deny: string[] }).deny).toEqual(["15550100002", "First", "Second"]);

    // The bridge going away: the page says so beside the status, and once as a notification.
    fresh.bridge.mode = "down";
    await page.goto(`${fresh.url}/status`);
    await expect(page.getByText("Bridge status unavailable")).toBeVisible();
    await expect(notifications(page).getByText("The bridge status is unavailable.")).toHaveCount(1);
    fresh.bridge.mode = "connected";
  });
});

test("UX-05 a destructive action asks first: restoring a default, removing a list item, a profile or an endpoint", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json", "monitor_profiles.json", "webhooks.json"] }, async (fresh) => {
    await fresh.signIn(page);
    const dialog = page.getByRole("alertdialog");
    // A list item.
    await openSection(page, fresh, "send_policy");
    await page.getByRole("button", { name: "Remove item 1 of Allow" }).click();
    await expect(dialog).toContainText("Remove this item?");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("textbox", { name: "Allow, item 1" })).toHaveValue("Example Group");
    await page.getByRole("button", { name: "Remove item 1 of Allow" }).click();
    await confirm(page, "Remove");
    await expect(page.getByRole("textbox", { name: "Allow, item 1" })).toHaveValue("15550100001");
    // A saved value going back to its default.
    await page.getByRole("button", { name: "Restore the default of Deny" }).click();
    await expect(dialog).toContainText("Restore the default?");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("textbox", { name: "Deny, item 1" })).toBeVisible();
    // A monitor profile.
    await openSection(page, fresh, "monitor");
    await page.getByRole("button", { name: /^Remove assistant/ }).click();
    await expect(dialog).toContainText("Remove this record?");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("group", { name: "Profiles" })).toContainText("assistant");
    await page.getByRole("button", { name: /^Remove assistant/ }).click();
    await confirm(page, "Remove");
    await expect(page.getByRole("group", { name: "Profiles" })).not.toContainText("assistant");
    // A webhook endpoint.
    await openSection(page, fresh, "webhooks");
    await page.getByRole("button", { name: /^Remove example/ }).click();
    await expect(dialog).toContainText("Remove this record?");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("group", { name: "Endpoints" })).toContainText("example");
    await page.getByRole("button", { name: /^Remove example/ }).click();
    await confirm(page, "Remove");
    await expect(page.getByRole("group", { name: "Endpoints" })).not.toContainText("example");
    // Nothing was written by any of it: removal is a draft until Save.
    expect(json(fresh, "webhooks.json")).toEqual(JSON.parse(readFileSync(join(SERVER_DIR, "tests", "fixtures", "ui", "webhooks.json"), "utf8")));
  });
});

/** A Notification API the test controls, in place before the page loads: what it says now, and what a request is answered. */
async function stubNotifications(page: Page, permission: "default" | "granted", answer: "denied" | "granted"): Promise<void> {
  await page.addInitScript(({ initial, reply }) => {
    const shown: string[] = [];
    let asked = 0;
    const Stub = Object.assign(
      function Stub(_title: string, options?: { body?: string }): void {
        shown.push(options?.body ?? "");
      },
      {
        permission: initial,
        requestPermission: (): Promise<string> => {
          asked += 1;
          return Promise.resolve(reply);
        },
      },
    );
    Object.assign(window, { Notification: Stub, __native: shown, __asked: () => asked });
  }, { initial: permission, reply: answer });
}

test("UX-06 without the browser's permission for notifications the page shows the notice itself", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json"] }, async (fresh) => {
    await stubNotifications(page, "default", "denied");
    await fresh.signIn(page);
    await openSection(page, fresh, "send_policy");
    await field(page, "deny").getByRole("button", { name: "Add item" }).click();
    await page.getByRole("textbox", { name: "Deny, item 2" }).fill("One");
    await save(page);
    // It asked on the first Save, was refused, and the notice is in the page.
    expect(await page.evaluate(() => (window as unknown as { __asked: () => number }).__asked())).toBe(1);
    await expect(page.getByRole("region", { name: "Notifications" }).getByText("Changes saved.")).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __native: string[] }).__native)).toEqual([]);
  });
});

test("UX-06 with permission, a tab in the background gets the browser's own notification instead", async ({ page }) => {
  await withHarness({ seed: ["send_policy.json"] }, async (fresh) => {
    await stubNotifications(page, "granted", "granted");
    await fresh.signIn(page);
    await openSection(page, fresh, "send_policy");
    await field(page, "deny").getByRole("button", { name: "Add item" }).click();
    await page.getByRole("textbox", { name: "Deny, item 2" }).fill("One");
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    });
    await saveButton(page).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __native: string[] }).__native)).toEqual(["Changes saved."]);
  });
});
