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

const fields = [
  f({ key: "name", label: "Name", kind: "text", default: "none", help: "Shown in logs." }),
  f({ key: "limit", label: "Limit", kind: "int", bounds: [1, 100] }),
  f({ key: "mode", label: "Mode", kind: "choice", options: ["block", "off"], default: "block", risk: "Off lets text pass between chats.", risk_when: ["off"] }),
  f({ key: "token", label: "Token", kind: "secret" }),
  f({ key: "fixed", label: "Fixed", kind: "text", env_var: "WHATSAPP_FIXED" }),
];

function section(overrides: Partial<SectionView> = {}): SectionView {
  return {
    id: "s", title: "Test section", description: "What this section does.", restart: "bridge", status: "ready", banner: null,
    warnings: [], version: "v1", read_only: false, fields,
    values: { name: "alpha", limit: 5, mode: "block", token: { set: true }, fixed: "from env" },
    state: {
      name: { origin: "file", locked: false, pending: null },
      limit: { origin: "default", locked: false, pending: null },
      mode: { origin: "file", locked: false, pending: null },
      token: { origin: "file", locked: false, pending: null },
      fixed: { origin: "environment", locked: true, pending: null },
    },
    ...overrides,
  };
}

const okOutcome: SaveOutcome = { ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: ["bridge"] };
const listing = [
  { id: "s", title: "Test section", icon: "shield", order: 10, kind: "section", description: "What this section does.", restart: "bridge" },
];

let viewport: ReturnType<typeof mockViewport>;
let current: SectionView;
let puts: unknown[];

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

const field = (name: string) => screen.getByLabelText(name);

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  notices.resetNoticeMemory();
  current = section();
  puts = [];
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(listing)),
    http.get("*/api/sections/s", () => HttpResponse.json(current)),
    http.post("*/api/sections/s/validate", () => HttpResponse.json({ errors: {}, general: [], can_save: true })),
    http.put("*/api/sections/s", async ({ request }) => {
      puts.push(await request.json());
      return HttpResponse.json(okOutcome);
    }),
  );
});
afterEach(() => {
  viewport.restore();
  queryClient.setDefaultOptions({});
  vi.unstubAllGlobals();
});

describe("cards", () => {
  it("UXP-14 titled cards: the form is one titled card per group, fields without a group in General", async () => {
    current = section({
      fields: [
        f({ key: "name", label: "Name" }),
        f({ key: "limit", label: "Limit", kind: "int", group: "Timing" }),
        f({ key: "mode", label: "Mode", group: "Timing" }),
      ],
      values: { name: "alpha", limit: 5, mode: "x" },
      state: {},
    });
    await mount();
    const general = screen.getByRole("region", { name: "General" });
    const timing = screen.getByRole("region", { name: "Timing" });
    expect(within(general).getByLabelText("Name")).toBeInTheDocument();
    expect(within(general).queryByLabelText("Limit")).toBeNull();
    expect(within(timing).getByLabelText("Limit")).toBeInTheDocument();
    expect(within(timing).getByLabelText("Mode")).toBeInTheDocument();
    expect(within(timing).getByRole("heading", { level: 2, name: "Timing" })).toBeInTheDocument();
  });

  it("UXP-14 a section with no labels keeps a single card", async () => {
    await mount();
    expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 2 })).toHaveLength(1);
    expect(screen.getByRole("region", { name: "General" })).toBeInTheDocument();
  });

  it("UXP-16 a records or list field is its own card", async () => {
    current = section({
      fields: [f({ key: "name", label: "Name" }), f({ key: "targets", label: "Targets", kind: "list" })],
      values: { name: "alpha", targets: ["one"] },
      state: {},
    });
    await mount();
    const card = screen.getByRole("region", { name: "Targets" });
    expect(within(card).getByRole("heading", { level: 2, name: "Targets" })).toBeInTheDocument();
    expect(within(card).getByLabelText("Targets, item 1")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "General" })).queryByLabelText("Targets, item 1")).toBeNull();
  });
});

describe("card error marker", () => {
  it("UXP-19 a card with an invalid field is marked and the summary link still focuses the field", async () => {
    current = section({
      fields: [
        f({ key: "name", label: "Name" }),
        f({ key: "limit", label: "Limit", kind: "int", group: "Timing" }),
      ],
      values: { name: "alpha", limit: 5 },
      state: {},
    });
    server.use(
      http.post("*/api/sections/s/validate", () =>
        HttpResponse.json({ errors: { limit: "must be a whole number" }, general: [], can_save: false }),
      ),
      http.put("*/api/sections/s", () =>
        HttpResponse.json({ ...okOutcome, ok: false, restarts: [], errors: { limit: "must be a whole number" }, general: [] }, { status: 422 }),
      ),
    );
    await mount();
    expect(within(screen.getByRole("region", { name: /^General/ })).queryByText("needs attention")).toBeNull();
    await userEvent.type(field("Limit"), "z");
    const timing = await screen.findByRole("region", { name: /^Timing/ });
    await waitFor(() => {
      expect(within(timing).getByText("needs attention")).toBeInTheDocument();
    });
    expect(timing.querySelector("[data-needs-attention] svg")).not.toBeNull();
    expect(within(timing).getByRole("heading", { level: 2 })).toHaveTextContent("Timing");
    expect(within(screen.getByRole("region", { name: /^General/ })).queryByText("needs attention")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const summary = await screen.findByRole("alert");
    await userEvent.click(within(summary).getByRole("button", { name: "Go to Limit" }));
    expect(field("Limit")).toHaveFocus();
  });
});

describe("loading and failing", () => {
  it("shows a skeleton while the section loads", async () => {
    server.use(http.get("*/api/sections/s", async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
      return HttpResponse.json(current);
    }));
    const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/s"] }));
    render(
      <AppProviders router={router}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    expect(await screen.findByLabelText("Loading")).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });

  it("shows an error with a retry that loads the section", async () => {
    queryClient.setDefaultOptions({ queries: { retry: false } });
    server.use(http.get("*/api/sections/s", () => HttpResponse.json({ code: "bad_request", message: "Boom." }, { status: 500 })));
    const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/s"] }));
    render(
      <AppProviders router={router}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    expect(await screen.findByText("This section could not be loaded.")).toBeInTheDocument();
    server.use(http.get("*/api/sections/s", () => HttpResponse.json(current)));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });
});

describe("the form built from the schema", () => {
  it("shows every field of the schema with the description and the restart note", async () => {
    await mount();
    expect(within(screen.getByRole("banner")).getByText("What this section does.")).toBeInTheDocument();
    expect(screen.getByText("Changes here apply after the bridge restarts.")).toBeInTheDocument();
    expect(field("Name")).toHaveValue("alpha");
    expect(field("Limit")).toHaveValue("5");
    expect(screen.getByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Token" })).toHaveTextContent("Set");
    expect(field("Fixed")).toHaveAttribute("readonly");
    expect(screen.getByText(/WHATSAPP_FIXED/)).toBeInTheDocument();
  });

  it("marks a field that waits for a restart", async () => {
    current = section({ state: { ...section().state, name: { origin: "file", locked: false, pending: "bridge" } } });
    await mount();
    expect(screen.getByText("Restart the bridge to apply")).toBeInTheDocument();
  });

  it("shows the file problem, the empty note and the warnings", async () => {
    current = section({ status: "error", banner: "The file is not valid JSON.", warnings: ["Heads up."] });
    await mount();
    expect(screen.getByText("The saved file has a problem")).toBeInTheDocument();
    expect(screen.getByText("The file is not valid JSON.")).toBeInTheDocument();
    expect(screen.getByText("Heads up.")).toBeInTheDocument();
  });

  it("says when nothing is saved yet", async () => {
    current = section({ status: "empty" });
    await mount();
    expect(screen.getByText("Nothing is saved here yet. The defaults are in use.")).toBeInTheDocument();
  });

  it("says when the section is unavailable and gives the reason", async () => {
    current = section({ status: "unavailable", banner: "The bridge is not responding." });
    await mount();
    expect(screen.getByText("Not available right now")).toBeInTheDocument();
    expect(screen.getByText("The bridge is not responding.")).toBeInTheDocument();
  });
});

describe("read-only sections", () => {
  it("show the values and no Save, Discard or Restore defaults, and the controls cannot change", async () => {
    current = section({ read_only: true });
    await mount();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore defaults" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
    expect(field("Name")).toHaveAttribute("readonly");
    expect(field("Name")).toHaveValue("alpha");
  });
});

describe("saving", () => {
  it("keeps Save and Discard away until something changes", async () => {
    await mount();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard changes" })).toBeNull();
    await userEvent.type(field("Name"), "!");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(field("Name")).toHaveValue("alpha");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("saves the changes, confirms with a notice and records the restart", async () => {
    await mount();
    await userEvent.clear(field("Name"));
    await userEvent.type(field("Name"), "beta");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(puts).toEqual([{ version: "v1", changes: { name: "beta" }, restore: [] }]);
    });
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(useUiStore.getState().restart).toEqual(["bridge"]);
  });

  it("saves from the keyboard with Enter in a field", async () => {
    await mount();
    await userEvent.type(field("Name"), "z{Enter}");
    await waitFor(() => {
      expect(puts).toHaveLength(1);
    });
  });

  it("a refusal that is not about a field says the changes could not be saved, not that a field needs attention", async () => {
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json(
          { ...okOutcome, ok: false, restarts: [], errors: {}, general: ["the file could not be written (Permission denied)"] },
          { status: 422 },
        ),
      ),
    );
    await mount();
    await userEvent.type(field("Name"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("The changes could not be saved");
    expect(summary).toHaveTextContent("the file could not be written (Permission denied)");
    expect(summary).not.toHaveTextContent("needs attention");
  });

  it("shows the server's errors next to the fields, summarises them and focuses the first", async () => {
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json(
          { ...okOutcome, ok: false, restarts: [], errors: { limit: "must be a whole number", name: "is too long" }, general: [] },
          { status: 422 },
        ),
      ),
    );
    await mount();
    await userEvent.type(field("Name"), "x");
    await userEvent.type(field("Limit"), "z");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("2 fields need attention");
    expect(field("Name")).toHaveAccessibleDescription(/is too long/);
    expect(field("Limit")).toHaveAccessibleDescription(/must be a whole number/);
    expect(field("Name")).toHaveAttribute("aria-invalid", "true");
    await waitFor(() => {
      expect(field("Name")).toHaveFocus();
    });
    expect(field("Limit")).toHaveValue("5z");
    const goToLimit = within(summary).getByRole("button", { name: "Go to Limit" });
    expect(goToLimit.getAttribute("aria-label")).toContain(goToLimit.textContent);
    expect(summary).toHaveTextContent("Limit: must be a whole number");
    await userEvent.click(goToLimit);
    expect(field("Limit")).toHaveFocus();
  });

  it("focuses the first place to type when the message belongs to a whole records field", async () => {
    const profiles = f({
      key: "profiles", label: "Profiles", kind: "records", id_key: "name",
      fields: [f({ key: "name", label: "Profile name", kind: "text" })],
    });
    current = section({
      fields: [profiles], values: { profiles: [] }, state: { profiles: { origin: "default", locked: false, pending: null } },
    });
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json({ ...okOutcome, ok: false, restarts: [], errors: { profiles: "every profile needs a name" }, general: [] }, { status: 422 }),
      ),
    );
    const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/s"] }));
    render(
      <AppProviders router={router}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Add record to Profiles" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("alert");
    await waitFor(() => {
      expect(screen.getByLabelText("Profile name")).toHaveFocus();
    });
  });

  it("asks for the notification permission at the first Save, not on load", async () => {
    const requestPermission = vi.fn(() => Promise.resolve("granted" as NotificationPermission));
    vi.stubGlobal("Notification", { permission: "default", requestPermission });
    await mount();
    expect(requestPermission).not.toHaveBeenCalled();
    await userEvent.type(field("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(requestPermission).toHaveBeenCalledTimes(1);
  });

  it("offers a reload after a conflict and keeps the edits until it is used", async () => {
    server.use(http.put("*/api/sections/s", () => HttpResponse.json({ ...okOutcome, ok: false, conflict: true, restarts: [] }, { status: 409 })));
    await mount();
    await userEvent.type(field("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("The file changed on disk")).toBeInTheDocument();
    expect(field("Name")).toHaveValue("alpha!");
    current = section({ version: "v2", values: { ...section().values, name: "disk" } });
    await userEvent.click(screen.getByRole("button", { name: "Reload and discard my changes" }));
    await waitFor(() => {
      expect(field("Name")).toHaveValue("disk");
    });
    expect(screen.queryByText("The file changed on disk")).toBeNull();
  });

  it("asks before replacing an unreadable file and saves again with the confirmation", async () => {
    let call = 0;
    server.use(
      http.put("*/api/sections/s", async ({ request }) => {
        puts.push(await request.json());
        call += 1;
        return call === 1
          ? HttpResponse.json({ ...okOutcome, ok: false, needs_confirmation: true, restarts: [] }, { status: 409 })
          : HttpResponse.json(okOutcome);
      }),
    );
    await mount();
    await userEvent.type(field("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("The current file could not be read and will be replaced.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Replace file" }));
    await waitFor(() => {
      expect(puts).toHaveLength(2);
    });
    expect(puts[1]).toMatchObject({ confirm_replace: true });
  });
});

describe("restore defaults", () => {
  it("asks first; on confirm it restores saved fields, skips locked ones and says which", async () => {
    await mount();
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Restore defaults" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Every saved value of Test section goes back to its default.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore defaults" }));
    await waitFor(() => {
      expect(field("Name")).toHaveValue("none");
    });
    expect(field("Fixed")).toHaveValue("from env");
    expect(screen.getByText("Left unchanged because the environment sets them: Fixed.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(puts).toHaveLength(1);
    });
    const sent = puts[0] as { restore: string[]; changes: Record<string, unknown> };
    expect([...sent.restore].sort()).toEqual(["mode", "name", "token"]);
    expect(sent.restore).not.toContain("fixed");
  });

  it("changes nothing when the operator cancels", async () => {
    await mount();
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Restore defaults" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(field("Name")).toHaveValue("alpha");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("restores one field after its own confirmation", async () => {
    await mount();
    await userEvent.click(screen.getByRole("button", { name: "Restore the default of Name" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Restore default" }));
    await waitFor(() => {
      expect(field("Name")).toHaveValue("none");
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});
