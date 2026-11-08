import { render, screen, waitFor, within } from "@testing-library/react";
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
  id: "s", title: "Test section", description: "What this section does.", restart: "bridge", status: "ready", banner: null,
  warnings: [], version: "v1", read_only: false,
  fields: [f({ key: "name", label: "Name", default: "none" }), f({ key: "fixed", label: "Fixed", env_var: "WHATSAPP_FIXED" })],
  values: { name: "alpha", fixed: "from env" },
  state: {
    name: { origin: "file", locked: false, pending: null },
    fixed: { origin: "environment", locked: true, pending: null },
  },
};
const okOutcome: SaveOutcome = { ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: [] };
const entry = { id: "s", title: "Test section", icon: "shield", order: 10, kind: "section", description: "What this section does.", restart: "bridge" };

let viewport: ReturnType<typeof mockViewport>;
let puts: unknown[];

async function mount(options: { read_only?: boolean; pending?: boolean } = {}) {
  server.use(
    http.get("*/api/sections", () => HttpResponse.json([{ ...entry, pending: options.pending === true }])),
    http.get("*/api/sections/s", () => HttpResponse.json({ ...view, read_only: options.read_only === true })),
  );
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/s"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  await screen.findByLabelText("Name");
}

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  notices.resetNoticeMemory();
  puts = [];
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
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

const header = () => within(screen.getByRole("banner"));

describe("section header", () => {
  it("UXP-24 the header shows title, description and the restart badge", async () => {
    await mount({ pending: true });
    expect(await header().findByRole("heading", { level: 1, name: "Test section" })).toBeInTheDocument();
    expect(header().getByText("What this section does.")).toBeInTheDocument();
    expect(await header().findByText("Restart needed")).toBeInTheDocument();
  });

  it("UXP-24 no badge while no restart is waiting", async () => {
    await mount();
    expect(await header().findByRole("heading", { level: 1, name: "Test section" })).toBeInTheDocument();
    expect(header().queryByText("Restart needed")).toBeNull();
  });

  it("UXP-23 restore defaults is in More actions in the header and still confirms", async () => {
    await mount();
    expect(screen.queryByRole("button", { name: "Restore defaults" })).toBeNull();
    const more = await header().findByRole("button", { name: "More actions" });
    await userEvent.click(more);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Restore defaults" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Every saved value of Test section goes back to its default.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore defaults" }));
    await waitFor(() => {
      expect(screen.getByLabelText("Name")).toHaveValue("none");
    });
    // The locked field is left alone and the note says so.
    expect(screen.getByLabelText("Fixed")).toHaveValue("from env");
    expect(screen.getByText("Left unchanged because the environment sets them: Fixed.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(puts).toHaveLength(1);
    });
    expect(puts[0]).toMatchObject({ restore: ["name"] });
  });

  it("UXP-23 the menu opens by keyboard and Escape closes it without restoring", async () => {
    await mount();
    const more = await header().findByRole("button", { name: "More actions" });
    more.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("menuitem", { name: "Restore defaults" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("menuitem")).toBeNull();
    });
    expect(more).toHaveFocus();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("UXP-23 a read-only section has no More actions", async () => {
    await mount({ read_only: true });
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });
});
