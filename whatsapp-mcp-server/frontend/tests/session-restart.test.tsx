import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { resetNoticeMemory } from "../src/lib/notices";
import { queryClient } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import type { FieldSchema, SaveOutcome, SectionView } from "../src/types/api";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const field: FieldSchema = {
  key: "name", label: "Name", kind: "text", default: "none", help: "", secret: false, env_var: null, options: [], bounds: null,
  max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
};
const view: SectionView = {
  id: "s", title: "Test section", description: "What it does.", restart: "bridge", status: "ready", banner: null, warnings: [],
  version: "v1", read_only: false, fields: [field], values: { name: "alpha" },
  state: { name: { origin: "file", locked: false, pending: null } },
};
const saved: SaveOutcome = { ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: ["bridge"] };
const listing = [{ id: "s", title: "Test section", icon: "shield", order: 10, kind: "section", description: "", restart: "bridge" }];

let viewport: ReturnType<typeof mockViewport>;

async function mount() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/s"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  await screen.findByLabelText("Name");
  return router;
}

const marker = async () => {
  const nav = await screen.findByRole("navigation", { name: "Sections" });
  return within(await within(nav).findByRole("link", { name: /Test section/ })).queryByText("Restart needed");
};

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  resetNoticeMemory();
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(listing)),
    http.get("*/api/sections/s", () => HttpResponse.json(view)),
    http.post("*/api/sections/s/validate", () => HttpResponse.json({ errors: {}, general: [], can_save: true })),
    http.put("*/api/sections/s", () => HttpResponse.json(saved)),
  );
});
afterEach(() => {
  viewport.restore();
});

describe("a session that ends while a restart marker is showing (CFG-08)", () => {
  it("save, the session is lost (401), sign in again: the marker is gone when the server reports none", async () => {
    const router = await mount();
    await userEvent.type(screen.getByLabelText("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(async () => {
      expect(await marker()).toBeInTheDocument();
    });
    expect(useUiStore.getState().restartSections).toEqual(["s"]);

    // The bridge restarts: the next write finds the session gone.
    await userEvent.type(screen.getByLabelText("Name"), "?");
    server.use(
      http.put("*/api/sections/s", () => HttpResponse.json({ code: "session_expired", message: "Your session ended." }, { status: 401 })),
      http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "" })),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    await screen.findByLabelText("Password");
    expect(useUiStore.getState().restart).toEqual([]);
    expect(useUiStore.getState().restartSections).toEqual([]);
    expect(useUiStore.getState().draftDirty).toBe(false);

    server.use(
      http.post("*/api/session/login", () => HttpResponse.json({ ok: true, csrf: "c2" })),
      http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c2", idle_remaining: 900 })),
    );
    await userEvent.type(screen.getByLabelText("Password"), "correct horse{Enter}");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/s");
    });
    await screen.findByLabelText("Name");
    expect(await marker()).toBeNull();
  });

  it("still shows the marker the server reports after signing in again", async () => {
    server.use(http.get("*/api/sections", () => HttpResponse.json(listing.map((entry) => ({ ...entry, pending: true })))));
    await mount();
    expect(await marker()).toBeInTheDocument();
  });
});

describe("signing out with a real edit", () => {
  it("typing in a section and pressing Sign out asks before dropping the edit; Stay keeps it", async () => {
    let deleted = 0;
    server.use(
      http.delete("*/api/session", () => {
        deleted += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    const router = await mount();
    expect(useUiStore.getState().draftDirty).toBe(false);
    await userEvent.type(screen.getByLabelText("Name"), "!");
    expect(useUiStore.getState().draftDirty).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Sign out without saving?" });
    expect(deleted).toBe(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Stay" }));
    expect(screen.getByLabelText("Name")).toHaveValue("alpha!");
    expect(router.state.location.pathname).toBe("/s/s");
  });

  it("signs out at once when nothing was edited", async () => {
    let deleted = 0;
    server.use(
      http.delete("*/api/session", () => {
        deleted += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    await mount();
    server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "" })));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(deleted).toBe(1);
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
