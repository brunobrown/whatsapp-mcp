import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { queryClient } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const sections = [
  { id: "status", title: "Status", icon: "activity", order: 0, kind: "page", description: "Bridge and counters", restart: null },
  { id: "send_policy", title: "Send policy", icon: "shield", order: 10, kind: "section", description: "Who may send", restart: "server" },
  { id: "media", title: "Media downloads", icon: "image", order: 70, kind: "section", description: "Where files go", restart: "bridge" },
  { id: "account", title: "Account", icon: "user", order: 100, kind: "page", description: "Password", restart: null },
];

let viewport: ReturnType<typeof mockViewport>;

/** The section list once its links have loaded. */
async function listed(): Promise<HTMLElement> {
  const nav = await screen.findByRole("navigation", { name: "Sections" });
  await within(nav).findAllByRole("link");
  return nav;
}

function open(path: string) {
  const router = createAppRouter(createMemoryHistory({ initialEntries: [path] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

beforeEach(() => {
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(sections)),
    // /status now renders the status page, which asks the bridge; these tests are about the shell around it.
    http.get("*/api/ops/status", () => HttpResponse.json({ unavailable: "unreachable", message: "x" })),
    http.get("*/api/ops/counters", () => HttpResponse.json({ unavailable: "unreachable", message: "x" })),
  );
});
afterEach(() => {
  viewport.restore();
});

describe("wide screens (600 px and more)", () => {
  beforeEach(() => {
    viewport = mockViewport(600);
  });

  it("shows the section list and the detail together, built from the sections endpoint", async () => {
    open("/s/media");
    const nav = await listed();
    expect(within(nav).getAllByRole("link").map((l) => l.textContent)).toEqual([
      expect.stringContaining("Status"),
      expect.stringContaining("Send policy"),
      expect.stringContaining("Media downloads"),
      expect.stringContaining("Account"),
    ]);
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Media downloads" })).toBeInTheDocument();
  });

  it("marks the open section as the current page and lists each description", async () => {
    open("/s/media");
    const nav = await listed();
    expect(within(nav).getByRole("link", { name: /Media downloads/ })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: /Send policy/ })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByText("Who may send")).toBeInTheDocument();
  });

  it("opens a page of the list with a click", async () => {
    const router = open("/s/media");
    const nav = await listed();
    await userEvent.click(within(nav).getByRole("link", { name: /Account/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/account");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Account" })).toBeInTheDocument();
  });

  it("shows the pending-restart marker only on a section whose restart is pending", async () => {
    useUiStore.getState().addRestart(["bridge"], "media");
    open("/s/send_policy");
    const nav = await listed();
    expect(within(within(nav).getByRole("link", { name: /Media downloads/ })).getByText("Restart needed")).toBeInTheDocument();
    expect(within(within(nav).getByRole("link", { name: /Send policy/ })).queryByText("Restart needed")).toBeNull();
  });

  it("shows the marker the server reports even when nothing was saved in this page (after a reload)", async () => {
    server.use(
      http.get("*/api/sections", () =>
        HttpResponse.json(sections.map((entry) => ({ ...entry, pending: entry.id === "media" }))),
      ),
    );
    open("/s/media");
    const nav = await listed();
    expect(useUiStore.getState().restart).toEqual([]);
    expect(within(within(nav).getByRole("link", { name: /Media downloads/ })).getByText("Restart needed")).toBeInTheDocument();
    expect(within(within(nav).getByRole("link", { name: /Send policy/ })).queryByText("Restart needed")).toBeNull();
    expect(screen.getAllByText("Restart needed")).toHaveLength(2);
  });

  it("shows the marker in the header of the open section too", async () => {
    useUiStore.getState().addRestart(["bridge"], "media");
    open("/s/media");
    expect(await screen.findAllByText("Restart needed")).toHaveLength(2);
  });

  it("marks only the section that was saved, not the others that restart the same process", async () => {
    server.use(
      http.get("*/api/sections", () =>
        HttpResponse.json([...sections, { ...sections[2], id: "webhooks", title: "Webhooks", order: 75 }]),
      ),
    );
    useUiStore.getState().addRestart(["bridge"], "media");
    open("/s/media");
    const nav = await listed();
    await within(nav).findByRole("link", { name: /Webhooks/ });
    expect(within(within(nav).getByRole("link", { name: /Media downloads/ })).getByText("Restart needed")).toBeInTheDocument();
    expect(within(within(nav).getByRole("link", { name: /Webhooks/ })).queryByText("Restart needed")).toBeNull();
  });

  it("keeps the brand header, the theme and sign-out buttons and the section list in one landmark", async () => {
    open("/s/send_policy");
    const nav = await listed();
    const panel = screen.getByRole("complementary", { name: "Settings menu" });
    expect(within(panel).getByRole("navigation", { name: "Sections" })).toBe(nav);
    expect(within(panel).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /^Theme:/ })).toBeInTheDocument();
    expect(within(panel).getByText("WhatsApp MCP")).toBeInTheDocument();
  });

  it("has no back button", async () => {
    open("/s/media");
    await listed();
    expect(screen.queryByRole("button", { name: "Back to the sections" })).toBeNull();
  });

  it("keeps the layout inside the viewport width", async () => {
    open("/s/media");
    await listed();
    const root = screen.getByTestId("shell");
    expect(root).toHaveClass("overflow-hidden");
    expect(screen.getByRole("main")).toHaveClass("min-w-0");
  });
});

describe("narrow screens (below 600 px)", () => {
  beforeEach(() => {
    viewport = mockViewport(360);
  });

  it("shows the list alone first, then the detail alone after a choice, and back returns to the list", async () => {
    open("/s/media");
    const nav = await listed();
    expect(screen.queryByRole("main")).toBeNull();

    await userEvent.click(within(nav).getByRole("link", { name: /Send policy/ }));
    expect(await screen.findByRole("main")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Send policy" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Back to the sections" }));
    expect(await screen.findByRole("navigation", { name: "Sections" })).toBeInTheDocument();
    expect(screen.queryByRole("main")).toBeNull();
  });

  it("reaches back by keyboard", async () => {
    open("/s/media");
    const nav = await listed();
    await userEvent.click(within(nav).getByRole("link", { name: /Account/ }));
    const back = await screen.findByRole("button", { name: "Back to the sections" });
    back.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("navigation", { name: "Sections" })).toBeInTheDocument();
  });

  it("switches to two panes when the window grows past 600 px", async () => {
    open("/s/media");
    await listed();
    expect(screen.queryByRole("main")).toBeNull();
    act(() => {
      viewport.setWidth(768);
    });
    expect(await screen.findByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Sections" })).toBeInTheDocument();
  });
});

describe("states and header actions", () => {
  beforeEach(() => {
    viewport = mockViewport(1280);
    queryClient.setDefaultOptions({ queries: { retry: false } });
  });
  afterEach(() => {
    queryClient.setDefaultOptions({});
  });

  it("offers a retry when the sections cannot be loaded", async () => {
    server.use(http.get("*/api/sections", () => HttpResponse.json({ code: "x", message: "Down." }, { status: 500 })));
    open("/status");
    expect(await screen.findByText("The sections could not be loaded.")).toBeInTheDocument();
    server.use(http.get("*/api/sections", () => HttpResponse.json(sections)));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await listed()).toBeInTheDocument();
  });

  it("cycles the theme from the header button", async () => {
    open("/status");
    await listed();
    expect(useUiStore.getState().theme).toBe("system");
    await userEvent.click(screen.getByRole("button", { name: /^Theme: System/ }));
    expect(useUiStore.getState().theme).toBe("light");
    await userEvent.click(screen.getByRole("button", { name: /^Theme: Light/ }));
    expect(useUiStore.getState().theme).toBe("dark");
  });

  it("signs out: calls DELETE /session and lands on /login", async () => {
    let deleted = false;
    server.use(
      http.delete("*/api/session", () => {
        deleted = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const router = open("/status");
    await listed();
    server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "c" })));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(deleted).toBe(true);
  });

  it("asks before signing out with unsaved edits: staying keeps the session, confirming ends it", async () => {
    let deleted = 0;
    server.use(
      http.delete("*/api/session", () => {
        deleted += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    const router = open("/status");
    await listed();
    useUiStore.getState().setDraftDirty(true);

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Sign out without saving?" });
    expect(deleted).toBe(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(deleted).toBe(0);
    expect(router.state.location.pathname).toBe("/status");

    server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "c" })));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard and sign out" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(deleted).toBe(1);
  });

  it("offers a skip link to the content", async () => {
    open("/status");
    await listed();
    expect(screen.getByRole("link", { name: "Skip to the content" })).toHaveAttribute("href", "#content");
  });
});
