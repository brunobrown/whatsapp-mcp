import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createBrowserHistory, createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { api } from "../src/lib/api";
import { queryClient } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import type { FieldSchema, SectionView } from "../src/types/api";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const name: FieldSchema = {
  key: "name", label: "Name", kind: "text", default: "", help: "", secret: false, env_var: null, options: [], bounds: null,
  max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
};

function view(id: string, title: string): SectionView {
  return {
    id, title, description: "", restart: null, status: "ready", banner: null, warnings: [], version: "v1", read_only: false,
    fields: [name], values: { name: `${id}-value` }, state: { name: { origin: "file", locked: false, pending: null } },
  };
}

const listing = [
  { id: "a", title: "Section A", icon: "shield", order: 10, kind: "section", description: "", restart: null },
  { id: "b", title: "Section B", icon: "eye", order: 20, kind: "section", description: "", restart: null },
];

let viewport: ReturnType<typeof mockViewport>;

async function mount() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/a"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  await screen.findByLabelText("Name");
  return router;
}

const nav = () => screen.getByRole("navigation", { name: "Sections" });

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(listing)),
    http.get("*/api/sections/a", () => HttpResponse.json(view("a", "Section A"))),
    http.get("*/api/sections/b", () => HttpResponse.json(view("b", "Section B"))),
    http.post("*/api/sections/a/validate", () => HttpResponse.json({ errors: {}, general: [], can_save: true })),
    http.put("*/api/sections/a", () =>
      HttpResponse.json({ ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: [] }),
    ),
  );
});
afterEach(() => {
  viewport.restore();
});

describe("unsaved changes", () => {
  it("a clean section is left without asking", async () => {
    const router = await mount();
    await userEvent.click(within(nav()).getByRole("link", { name: /Section B/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/b");
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("leaving a dirty section asks first and stays put until answered", async () => {
    const router = await mount();
    await userEvent.type(screen.getByLabelText("Name"), "!");
    await userEvent.click(within(nav()).getByRole("link", { name: /Section B/ }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("You have changes in Section A that are not saved.");
    expect(router.state.location.pathname).toBe("/s/a");
  });

  it("staying keeps the page and the draft", async () => {
    const router = await mount();
    await userEvent.type(screen.getByLabelText("Name"), "!");
    await userEvent.click(within(nav()).getByRole("link", { name: /Section B/ }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Stay" }));
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });
    expect(router.state.location.pathname).toBe("/s/a");
    expect(screen.getByLabelText("Name")).toHaveValue("a-value!");
  });

  it("choosing to leave goes on to the other section, which starts clean", async () => {
    const router = await mount();
    await userEvent.type(screen.getByLabelText("Name"), "!");
    await userEvent.click(within(nav()).getByRole("link", { name: /Section B/ }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard and leave" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/b");
    });
    expect(await screen.findByLabelText("Name")).toHaveValue("b-value");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("the browser's back is guarded too", async () => {
    window.history.replaceState({}, "", "/status");
    window.history.pushState({}, "", "/s/a");
    const router = createAppRouter(createBrowserHistory());
    render(
      <AppProviders router={router}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    await userEvent.type(await screen.findByLabelText("Name"), "!");
    window.history.back();
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/s/a");
  });

  it("closing or reloading the tab gets the browser's prompt only while dirty", async () => {
    window.history.replaceState({}, "", "/s/a");
    const router = createAppRouter(createBrowserHistory());
    render(
      <AppProviders router={router}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    await screen.findByLabelText("Name");
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    await userEvent.type(screen.getByLabelText("Name"), "!");
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("a saved section is clean again and leaves without asking", async () => {
    const router = await mount();
    await userEvent.type(screen.getByLabelText("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    });
    await userEvent.click(within(nav()).getByRole("link", { name: /Section B/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/b");
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("a lost session goes to sign-in at once, without asking about the draft", async () => {
    const router = await mount();
    await userEvent.type(screen.getByLabelText("Name"), "!");
    server.use(
      http.get("*/api/x", () => HttpResponse.json({ code: "unauthenticated", message: "No." }, { status: 401 })),
      http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "n" })),
    );
    await api.get("/x").catch(() => undefined);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
