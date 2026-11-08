import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
  localStorage.clear();
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
    // 600 px is a rail by default; these tests read the full entries (title, description, badge), so they ask for the expanded menu.
    useUiStore.setState({ menu: "expanded" });
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
    expect(within(panel).getByRole("button", { name: /^Switch to \w+ theme$/ })).toBeInTheDocument();
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

  it("shows the open section alone under a header with the Open menu button; the menu is only in the drawer", async () => {
    open("/s/media");
    expect(await screen.findByRole("heading", { level: 1, name: "Media downloads" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("banner")).toContainElement(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryByRole("button", { name: "Back to the sections" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const nav = await listed();
    await userEvent.click(within(nav).getByRole("link", { name: /Send policy/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Send policy" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
  });

  it("reaches the menu and an entry by keyboard", async () => {
    const router = open("/s/media");
    await screen.findByRole("heading", { level: 1, name: "Media downloads" });
    screen.getByRole("button", { name: "Open menu" }).focus();
    await userEvent.keyboard("{Enter}");
    const nav = await listed();
    within(nav).getByRole("link", { name: /Account/ }).focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/account");
    });
  });

  it("switches to the menu beside the section when the window grows past 600 px, keeping the open section", async () => {
    open("/s/media");
    await screen.findByRole("heading", { level: 1, name: "Media downloads" });
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
    act(() => {
      viewport.setWidth(768);
    });
    expect(await screen.findByRole("navigation", { name: "Sections" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Media downloads" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
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
    await userEvent.click(screen.getByRole("button", { name: "Switch to Light theme" }));
    expect(useUiStore.getState().theme).toBe("light");
    await userEvent.click(screen.getByRole("button", { name: "Switch to Dark theme" }));
    expect(useUiStore.getState().theme).toBe("dark");
  });

  it("UXP-32 the theme button is named by the theme it switches to and shows that name as a tooltip", async () => {
    open("/status");
    await listed();
    const button = screen.getByRole("button", { name: "Switch to Light theme" });
    await userEvent.hover(button);
    expect((await screen.findAllByText("Switch to Light theme")).length).toBeGreaterThan(0);
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: "Switch to Dark theme" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Switch to Dark theme" }));
    expect(screen.getByRole("button", { name: "Switch to System theme" })).toBeInTheDocument();
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

describe("menu collapse", () => {
  const rail = () => screen.getByTestId("shell");

  it("UXP-01 collapse and expand with the right names and aria-expanded", async () => {
    viewport = mockViewport(1280);
    open("/s/media");
    const nav = await listed();
    expect(rail()).toHaveAttribute("data-menu", "expanded");
    const collapse = screen.getByRole("button", { name: "Collapse menu" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(collapse);
    const expand = screen.getByRole("button", { name: "Expand menu" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(rail()).toHaveAttribute("data-menu", "collapsed");
    // One icon per section, no visible title.
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(4);
    expect(links.every((link) => link.textContent === "")).toBe(true);
    expect(screen.queryByRole("button", { name: "Collapse menu" })).toBeNull();

    // The keyboard does the same: focus the button and press Enter.
    expand.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Collapse menu" })).toHaveAttribute("aria-expanded", "true");
    expect(rail()).toHaveAttribute("data-menu", "expanded");
    expect(within(nav).getByText("Who may send")).toBeInTheDocument();
  });

  it("UXP-01 the theme and sign-out buttons stay in the menu when it is a rail", async () => {
    viewport = mockViewport(800);
    open("/s/media");
    await listed();
    const panel = screen.getByRole("complementary", { name: "Settings menu" });
    expect(within(panel).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /^Switch to \w+ theme$/ })).toBeInTheDocument();
  });

  it("UXP-03 defaults by width and the stored choice", async () => {
    // No stored choice: a rail from 600 to 1023 px, expanded from 1024 px.
    viewport = mockViewport(1023);
    open("/s/media");
    await listed();
    expect(rail()).toHaveAttribute("data-menu", "collapsed");
    act(() => {
      viewport.setWidth(1024);
    });
    await waitFor(() => {
      expect(rail()).toHaveAttribute("data-menu", "expanded");
    });
    // The open section survives the change of width.
    expect(screen.getByRole("heading", { level: 1, name: "Media downloads" })).toBeInTheDocument();

    // The operator's choice wins at every width from 600 px and is read back after a reload.
    await userEvent.click(screen.getByRole("button", { name: "Collapse menu" }));
    const saved = localStorage.getItem("wmcp.ui") ?? "";
    expect(saved).toContain('"menu":"collapsed"');
    act(() => {
      viewport.setWidth(1400);
    });
    expect(rail()).toHaveAttribute("data-menu", "collapsed");
    cleanup();
    useUiStore.setState({ menu: "auto" });
    localStorage.setItem("wmcp.ui", saved);
    await useUiStore.persist.rehydrate();
    open("/s/media");
    await listed();
    expect(rail()).toHaveAttribute("data-menu", "collapsed");
    expect(screen.getByRole("button", { name: "Expand menu" })).toBeInTheDocument();
  });

  it("UXP-04 no button under 600", async () => {
    for (const width of [360, 599]) {
      cleanup();
      viewport = mockViewport(width);
      useUiStore.setState({ menu: "collapsed" });
      open("/s/media");
      await screen.findByRole("heading", { level: 1, name: "Media downloads" });
      expect(screen.queryByRole("button", { name: "Collapse menu" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Expand menu" })).toBeNull();
      // No rail either: the menu is the drawer, and its entries carry their titles as text.
      expect(rail()).toHaveAttribute("data-menu", "drawer");
      expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
      await userEvent.click(screen.getByRole("button", { name: "Open menu" }));
      expect(within(await listed()).getByText("Who may send")).toBeInTheDocument();
      viewport.restore();
    }
  });
});

describe("one scroller", () => {
  /** jsdom lays nothing out, so the element gets a scrollTop that holds what is written to it. */
  function scrollable(el: Element, top: number): void {
    let value = top;
    Object.defineProperty(el, "scrollTop", { configurable: true, get: () => value, set: (next: number) => void (value = next) });
  }

  it("UXP-13 route change resets the content scroll and keeps the menu scroll", async () => {
    viewport = mockViewport(1280);
    const router = open("/s/media");
    const nav = await listed();
    const menuScroller = nav.querySelector("[data-menu-scroll]") as HTMLElement;
    const main = screen.getByRole("main");
    scrollable(menuScroller, 180);
    scrollable(main, 420);

    await userEvent.click(within(nav).getByRole("link", { name: /Send policy/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
    expect(main.scrollTop).toBe(0);
    // The menu is the same element, not rebuilt, and still where the operator left it.
    expect(screen.getByRole("navigation", { name: "Sections" }).querySelector("[data-menu-scroll]")).toBe(menuScroller);
    expect(menuScroller.scrollTop).toBe(180);

    main.scrollTop = 90;
    await userEvent.click(within(nav).getByRole("link", { name: /Account/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/account");
    });
    expect(main.scrollTop).toBe(0);
    expect(menuScroller.scrollTop).toBe(180);
  });

  it("UXP-08 the shell is as tall as the window and is the only thing that fills it, with the content as the one scroller", async () => {
    viewport = mockViewport(360);
    open("/s/media");
    await screen.findByRole("heading", { level: 1, name: "Media downloads" });
    expect(screen.getByTestId("shell")).toHaveClass("h-dvh", "overflow-hidden");
    expect(screen.getByRole("banner")).toHaveClass("shrink-0");
    expect(screen.getByRole("main")).toHaveClass("overflow-y-auto", "min-h-0", "flex-1");
  });
});
