import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import * as notices from "../src/lib/notices";
import { queryClient } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import type { FieldSchema, SaveOutcome, SectionView } from "../src/types/api";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

function f(overrides: Partial<FieldSchema>): FieldSchema {
  return {
    key: "x", label: "X", kind: "text", default: null, help: "", secret: false, env_var: null, options: [], bounds: null,
    max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
    ...overrides,
  };
}

const view: SectionView = {
  id: "s", title: "Test section", description: "What this section does.", restart: null, status: "ready", banner: null,
  warnings: [], version: "v1", read_only: false,
  fields: [f({ key: "name", label: "Name" })],
  values: { name: "alpha" },
  state: { name: { origin: "file", locked: false, pending: null } },
};
const okOutcome: SaveOutcome = { ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: [] };
const listing = [{ id: "s", title: "Test section", icon: "shield", order: 10, kind: "section", description: "", restart: null }];

let viewport: ReturnType<typeof mockViewport>;
let puts: unknown[];

async function mount(current: SectionView = view) {
  server.use(http.get("*/api/sections/s", () => HttpResponse.json(current)));
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/s"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  await screen.findByLabelText("Name");
}

const name = () => screen.getByLabelText("Name");
const bar = () => document.querySelector<HTMLElement>("[data-save-bar]");

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  notices.resetNoticeMemory();
  puts = [];
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(listing)),
    http.post("*/api/sections/s/validate", () => HttpResponse.json({ errors: {}, general: [], can_save: true })),
    http.put("*/api/sections/s", async ({ request }) => {
      puts.push(await request.json());
      return HttpResponse.json(okOutcome);
    }),
  );
});
afterEach(() => {
  viewport.restore();
  vi.unstubAllGlobals();
});

describe("save bar", () => {
  it("UXP-20 no bar while the form has no unsaved edits", async () => {
    await mount();
    expect(bar()).toBeNull();
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    // An edit put back to the saved value is no edit.
    await userEvent.type(name(), "!");
    expect(bar()).not.toBeNull();
    await userEvent.type(name(), "{Backspace}");
    expect(bar()).toBeNull();
  });

  it("UXP-20 a read-only section never shows the bar", async () => {
    await mount({ ...view, read_only: true });
    expect(bar()).toBeNull();
    fireEvent.keyDown(name(), { key: "s", ctrlKey: true });
    expect(puts).toHaveLength(0);
  });

  it("UXP-21 the first edit shows a polite bar with the text, Discard changes and Save", async () => {
    await mount();
    await userEvent.type(name(), "!");
    const shown = bar();
    expect(shown).not.toBeNull();
    const status = within(shown as HTMLElement).getByRole("status");
    expect(status).toHaveTextContent("Unsaved changes");
    expect(within(shown as HTMLElement).getByRole("button", { name: "Discard changes" })).toBeEnabled();
    expect(within(shown as HTMLElement).getByRole("button", { name: "Save" })).toBeEnabled();
    // It sits under the content, outside the scrolling area, so it stays in view.
    expect(screen.getByRole("main").contains(shown)).toBe(false);
  });

  it("UXP-22 Ctrl+S inside the form saves and prevents the browser's save dialog", async () => {
    await mount();
    await userEvent.type(name(), "!");
    const event = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    name().dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    await waitFor(() => {
      expect(puts).toEqual([{ version: "v1", changes: { name: "alpha!" }, restore: [] }]);
    });
  });

  it("UXP-22 Cmd+S saves too, also with the focus on the bar's own buttons", async () => {
    await mount();
    await userEvent.type(name(), "!");
    const event = new KeyboardEvent("keydown", { key: "S", metaKey: true, bubbles: true, cancelable: true });
    screen.getByRole("button", { name: "Discard changes" }).dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    await waitFor(() => {
      expect(puts).toHaveLength(1);
    });
  });

  it("UXP-22 outside the section the key keeps its meaning and nothing is saved", async () => {
    await mount();
    await userEvent.type(name(), "!");
    const event = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    screen.getByRole("navigation").querySelector("a")?.dispatchEvent(event);
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true }));
    expect(event.defaultPrevented).toBe(false);
    // Another key with Ctrl is not the shortcut.
    const other = new KeyboardEvent("keydown", { key: "a", ctrlKey: true, bubbles: true, cancelable: true });
    name().dispatchEvent(other);
    expect(other.defaultPrevented).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(puts).toHaveLength(0);
  });

  it("UXP-22 with no edits Ctrl+S inside the form saves nothing", async () => {
    await mount();
    const event = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    name().dispatchEvent(event);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(puts).toHaveLength(0);
  });

  it("UXP-25 after a save the bar hides and Saved shows once as a notice", async () => {
    await mount();
    // The toast store outlives a test, so count what this save adds.
    const saved = (): number =>
      Array.from(document.querySelectorAll("[data-sonner-toast]")).filter((toast) => toast.textContent.includes("Changes saved.")).length;
    const before = saved();
    await userEvent.type(name(), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(bar()).toBeNull();
    });
    await waitFor(() => {
      expect(saved()).toBe(before + 1);
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(saved()).toBe(before + 1);
    // The old inline status text is gone: the section itself no longer says it.
    expect(within(screen.getByRole("main")).queryByText("Changes saved.")).toBeNull();
  });

  it("UXP-25 the focus does not fall to the page when the bar goes away", async () => {
    await mount();
    await userEvent.type(name(), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(bar()).toBeNull();
    });
    expect(document.activeElement).toBe(document.getElementById("content"));
  });
});
