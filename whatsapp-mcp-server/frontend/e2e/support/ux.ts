import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { test as base, expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";
import { Harness } from "./harness";

// Shared set-up of ux-navigation.spec.ts and ux-layout.spec.ts: one interface with an extension section, a long
// extension title, sixteen monitor profiles and a few dozen log rows, so every region has something to scroll.

/** Two sections dropped into the extension folder, the way EXT-01 does: one ordinary, one with a very long title. */
const EXTENSIONS: Record<string, string> = {
  "e2e_extra.py": `"""An extension section: it lands in the menu group More."""
import os

import localconfig
from ui.config.fields import Field
from ui.config.registry import JsonFile, Section

SECTION = Section(
    id="e2e_extra", title="Added at runtime", order=500, icon="radio",
    storage=JsonFile(lambda: os.path.join(localconfig.config_dir(), "e2e_extra.json")),
    fields=(Field("greeting", "Greeting", "text", default="hello"),),
    description="A section that was not in the menu when the interface started.",
)
`,
  "e2e_long.py": `"""An extension section whose title and description are far longer than the menu."""
import os

import localconfig
from ui.config.fields import Field
from ui.config.registry import JsonFile, Section

SECTION = Section(
    id="e2e_long", title=${JSON.stringify(longTitle())}, order=510, icon="radio",
    storage=JsonFile(lambda: os.path.join(localconfig.config_dir(), "e2e_long.json")),
    fields=(Field("note", "Note", "text", default=""),),
    description=${JSON.stringify(longDescription())},
)
`,
};

export function longTitle(): string {
  return "An extension section with a title much longer than the menu can ever show on one line";
}

export function longDescription(): string {
  return "Its description is long as well, so that the menu has to cut it with an ellipsis and keep the whole sentence in the title attribute";
}

/** Sixteen profiles: more records than fit in 24 rem. */
export function manyProfiles(count = 16): string {
  const profiles: Record<string, object> = {};
  for (let index = 1; index <= count; index += 1) {
    profiles[`profile-${String(index).padStart(2, "0")}`] = {
      enabled: true,
      mode: "draft",
      targets: ["15550100001"],
      aliases: [],
      triggers: ["mention"],
      topics: [],
      limits: { cooldown_seconds: 30 },
      senders: {},
      instructions: `Answer briefly (${String(index)}).`,
    };
  }
  return JSON.stringify({ profiles }, null, 2);
}

const MINUTE = 60_000;

/** The log rows the activity page reads: `count` drafts, sends and monitor replies, newest first. */
export function activityRows(count = 45): { drafts: object[]; send: object[]; monitor: object[] } {
  const base0 = Date.now() - 3 * 60 * MINUTE;
  const at = (index: number): string => new Date(base0 - index * MINUTE).toISOString();
  const later = (index: number): string => new Date(Date.now() + (index + 1) * 24 * 60 * MINUTE).toISOString();
  const rows = Array.from({ length: count }, (_, index) => index);
  return {
    drafts: rows.map((index) => ({ id: `d_${String(index)}`, profile: "assistant", chat_jid: `1203630000000000${String(index).padStart(2, "0")}@g.us`, sender: "15550100001", created_at: at(index), expires_at: later(index) })),
    send: rows.map((index) => ({ ts: at(index), kind: "text", chat_jid: `a${String(index)}@s.whatsapp.net`, chat_name: `Chat ${String(index)}`, size: 11, result: index % 2 === 0 ? "sent" : "blocked", rule: "allow", override: false, message_id: `s${String(index)}` })),
    // One profile per row: the Status page then lists more reply rows than fit in 24 rem.
    monitor: rows.map((index) => ({ ts: at(index), profile: `profile-${String(index % 16).padStart(2, "0")}`, chat_jid: "c@g.us", sender: "x", message_id: `m${String(index)}`, triggers: ["mention"], mode: "draft", approval_used: false, result: index % 3 === 0 ? "drafted" : "sent", size: 5 })),
  };
}

/** Starts one interface for the file: extension sections, sixteen profiles and the log rows are in place before it serves. */
export function uxHarness(): { readonly current: Harness } {
  const holder: { value: Harness | undefined } = { value: undefined };
  base.beforeAll(async () => {
    const harness = new Harness({ extraSections: true, seed: ["send_policy.json", "monitor_profiles.json", "webhooks.json", "channel.json", "ui_settings.json"] });
    holder.value = harness;
    for (const [name, source] of Object.entries(EXTENSIONS)) writeFileSync(join(harness.extraDir ?? "", name), source);
    harness.write("monitor_profiles.json", manyProfiles());
    const rows = activityRows();
    harness.seedActivity(rows);
    await harness.start();
  });
  base.afterAll(async () => {
    await holder.value?.stop();
    holder.value = undefined;
  });
  return {
    get current(): Harness {
      if (holder.value === undefined) throw new Error("The harness is not running.");
      return holder.value;
    },
  };
}

/** The menu panel (the aside that holds the section list) and the section list itself. */
export const panel = (page: Page): Locator => page.getByRole("complementary", { name: "Settings menu" });
export const menuNav = (page: Page): Locator => page.getByRole("navigation", { name: "Sections" });
export const shell = (page: Page): Locator => page.getByTestId("shell");

/** The width of an element in CSS pixels, read from the layout. */
export async function widthOf(locator: Locator): Promise<number> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error("The element has no box.");
  return box.width;
}

/** Whether the document itself scrolls in either direction: it must not (the shell is fixed to the window). */
export async function documentScrolls(page: Page): Promise<{ vertical: number; horizontal: number; scrolledTo: number }> {
  return page.evaluate(() => {
    const root = document.documentElement;
    window.scrollTo(0, 5000);
    return {
      vertical: root.scrollHeight - root.clientHeight,
      horizontal: root.scrollWidth - root.clientWidth,
      scrolledTo: window.scrollY,
    };
  });
}

/** Makes `localStorage` throw, as a browser with blocked site data does. Call before the first navigation. */
export async function blockStorage(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      get() {
        throw new DOMException("The operation is insecure.", "SecurityError");
      },
    });
  });
}

/** Lets the sections finish loading: the menu has its entries. */
export async function menuReady(page: Page): Promise<void> {
  await expect(page.locator("nav[aria-label='Sections'] a, [role=dialog] nav a").first()).toBeAttached();
}
