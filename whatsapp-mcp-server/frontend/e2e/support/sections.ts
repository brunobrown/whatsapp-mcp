import { expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";
import type { Harness } from "./harness";

/** Opens a section by its address and waits until its form is on the page. */
export async function openSection(page: Page, harness: Harness, id: string): Promise<void> {
  await page.goto(`${harness.url}/s/${id}`);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
}

/** The block of one field, found by the key the form marks it with. */
export function field(page: Page, key: string): Locator {
  return page.locator(`[data-field="${key}"]`);
}

export const saveButton = (page: Page): Locator => page.getByRole("button", { name: "Save", exact: true });

/** The confirmation that follows a save. */
export const savedNote = (page: Page): Locator => page.locator("p[role=status]").filter({ hasText: "Changes saved." });

/** Saves and waits for the confirmation next to the button. */
export async function save(page: Page): Promise<void> {
  await saveButton(page).click();
  await expect(savedNote(page)).toBeVisible();
}

/** The text of a file of the configuration directory as a parsed JSON value. */
export function json(harness: Harness, name: string): unknown {
  const text = harness.read(name);
  if (text === undefined) throw new Error(`${name} does not exist`);
  return JSON.parse(text);
}

/** Answers the open confirmation by the name of the button that confirms. */
export async function confirm(page: Page, button: string): Promise<void> {
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: button, exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

/**
 * Whether the page asks the browser to confirm before the tab is closed or reloaded. A browser decides by sending the
 * cancelable `beforeunload` event to the page and reading whether it was cancelled; this sends the same event.
 */
export async function unloadIsGuarded(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
}
