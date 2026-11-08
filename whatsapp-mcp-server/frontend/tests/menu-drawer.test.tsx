import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { queryClient } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

function section(id: string, title: string, description: string, order: number, kind: "section" | "page" = "section") {
  return { id, title, icon: "shield", order, kind, description, restart: null };
}
const sections = [
  section("status", "Status", "Bridge and counters", 0, "page"),
  section("send_policy", "Send policy", "Who may send", 10),
  section("monitor", "Monitor profiles", "Watched chats", 20),
  section("webhooks", "Webhooks", "Outgoing calls", 30),
  section("channel", "Channel", "Reply channel", 40),
  section("agent_safety", "Agent safety", "Limits for agents", 50),
  section("database", "Database", "Message store", 60),
  section("media", "Media downloads", "Where files go", 70),
  section("privacy", "Privacy", "What is kept", 80),
  section("account", "Account", "Password", 100, "page"),
];

let viewport: ReturnType<typeof mockViewport>;

function open(path = "/s/media") {
  const router = createAppRouter(createMemoryHistory({ initialEntries: [path] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

async function opened() {
  open();
  await screen.findByRole("heading", { level: 1, name: "Media downloads" });
  const opener = screen.getByRole("button", { name: "Open menu" });
  await userEvent.click(opener);
  const drawer = await screen.findByRole("dialog", { name: "Sections menu" });
  return { opener, drawer };
}

beforeEach(() => {
  localStorage.clear();
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  viewport = mockViewport(360);
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(sections)),
    // The open section is not what these tests are about; it only has to answer.
    http.get("*/api/sections/:id", () => HttpResponse.json({ code: "x", message: "Not here." }, { status: 404 })),
    http.get("*/api/ops/status", () => HttpResponse.json({ unavailable: "unreachable", message: "x" })),
    http.get("*/api/ops/counters", () => HttpResponse.json({ unavailable: "unreachable", message: "x" })),
  );
});
afterEach(() => {
  viewport.restore();
  vi.restoreAllMocks();
});

describe("menu drawer", () => {
  it("UXP-34 the section fills the screen under a header with Open menu, and the menu is only the drawer", async () => {
    open();
    expect(await screen.findByRole("heading", { level: 1, name: "Media downloads" })).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).getByRole("button", { name: "Open menu" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Sections" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("UXP-44 Open menu opens a drawer named Sections menu with the groups, the filter and every entry, and focus inside", async () => {
    const { drawer } = await opened();
    expect(within(drawer).getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Overview",
      "Messaging",
      "Safety and data",
      "Preferences",
    ]);
    expect(within(drawer).getByRole("searchbox", { name: "Filter sections" })).toBeInTheDocument();
    expect(within(drawer).getAllByRole("link")).toHaveLength(sections.length);
    expect(within(drawer).getByRole("button", { name: "Close menu" })).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: /^Switch to \w+ theme$/ })).toBeInTheDocument();
    expect(drawer).toContainElement(document.activeElement as HTMLElement);
    // The panel is limited to 320 px and 85% of the width.
    expect(drawer.className).toContain("w-[min(320px,85vw)]");
  });

  it("UXP-44 focus stays in the drawer while Tab goes around it", async () => {
    const { drawer } = await opened();
    for (let step = 0; step < 16; step += 1) {
      await userEvent.tab();
      expect(drawer).toContainElement(document.activeElement as HTMLElement);
    }
  });

  it("UXP-45 choosing an entry opens that section, closes the drawer and returns focus to Open menu", async () => {
    const router = open();
    await screen.findByRole("heading", { level: 1, name: "Media downloads" });
    const opener = screen.getByRole("button", { name: "Open menu" });
    await userEvent.click(opener);
    const drawer = await screen.findByRole("dialog", { name: "Sections menu" });
    await userEvent.click(within(drawer).getByRole("link", { name: /Send policy/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Send policy" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(opener).toHaveFocus();
  });

  it("UXP-45 Escape closes the drawer and returns focus to Open menu", async () => {
    const { opener } = await opened();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(opener).toHaveFocus();
  });

  it("UXP-45 the Close menu button closes the drawer and returns focus to Open menu", async () => {
    const { opener, drawer } = await opened();
    await userEvent.click(within(drawer).getByRole("button", { name: "Close menu" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(opener).toHaveFocus();
  });

  it("UXP-45 a tap on the overlay closes the drawer and returns focus to Open menu", async () => {
    const { opener } = await opened();
    const overlay = document.querySelector("[data-sheet-overlay]");
    expect(overlay).not.toBeNull();
    await userEvent.click(overlay as Element);
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(opener).toHaveFocus();
  });

  it("UXP-46 while the drawer is open the page behind it is hidden from assistive technology and takes no taps", async () => {
    const { drawer } = await opened();
    expect(screen.queryByRole("main")).toBeNull();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
    expect(document.body.style.pointerEvents).toBe("none");
    // Scroll lock of the document.
    expect(document.body.hasAttribute("data-scroll-locked")).toBe(true);
    expect(drawer).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.getByRole("main")).toBeInTheDocument();
    });
    expect(document.body.style.pointerEvents).not.toBe("none");
    expect(document.body.hasAttribute("data-scroll-locked")).toBe(false);
  });

  describe("swipe", () => {
    beforeEach(() => {
      vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(300);
    });

    async function swipe(distance: number) {
      const { drawer } = await opened();
      fireEvent.pointerDown(drawer, { pointerId: 7, pointerType: "touch", clientX: 280, clientY: 100 });
      fireEvent.pointerMove(drawer, { pointerId: 7, pointerType: "touch", clientX: 280 - distance, clientY: 104 });
      fireEvent.pointerUp(drawer, { pointerId: 7, pointerType: "touch", clientX: 280 - distance, clientY: 104 });
      return drawer;
    }

    it("UXP-47 swipe closes past a third", async () => {
      await swipe(101);
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
      expect(screen.getByRole("button", { name: "Open menu" })).toHaveFocus();
    });

    it("UXP-47 a shorter swipe leaves the drawer open and puts it back", async () => {
      const drawer = await swipe(99);
      expect(screen.getByRole("dialog", { name: "Sections menu" })).toBe(drawer);
      expect(drawer.style.transform).toBe("");
    });

    it("UXP-47 a vertical drag is the menu scrolling and does not close the drawer", async () => {
      const { drawer } = await opened();
      fireEvent.pointerDown(drawer, { pointerId: 8, pointerType: "touch", clientX: 200, clientY: 400 });
      fireEvent.pointerMove(drawer, { pointerId: 8, pointerType: "touch", clientX: 80, clientY: 700 });
      fireEvent.pointerUp(drawer, { pointerId: 8, pointerType: "touch", clientX: 80, clientY: 700 });
      expect(screen.getByRole("dialog", { name: "Sections menu" })).toBeInTheDocument();
    });
  });
});

describe("menu drawer and unsaved edits", () => {
  const name = {
    key: "name", label: "Name", kind: "text", default: "", help: "", secret: false, env_var: null, options: [], bounds: null,
    max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
  };
  const view = (id: string, title: string) => ({
    id, title, description: "", restart: null, status: "ready", banner: null, warnings: [], version: "v1", read_only: false,
    fields: [name], values: { name: `${id}-value` }, state: { name: { origin: "file", locked: false, pending: null } },
  });

  beforeEach(() => {
    server.use(
      http.get("*/api/sections/send_policy", () => HttpResponse.json(view("send_policy", "Send policy"))),
      http.get("*/api/sections/monitor", () => HttpResponse.json(view("monitor", "Monitor profiles"))),
      http.post("*/api/sections/send_policy/validate", () => HttpResponse.json({ errors: {}, general: [], can_save: true })),
    );
  });

  /** A real edit of the open section, then the drawer opened over it and another entry chosen. */
  async function chooseMonitorWithAnEdit() {
    const router = open("/s/send_policy");
    await userEvent.type(await screen.findByLabelText("Name"), "!");
    const opener = screen.getByRole("button", { name: "Open menu" });
    await userEvent.click(opener);
    const drawer = await screen.findByRole("dialog", { name: "Sections menu" });
    await userEvent.click(within(drawer).getByRole("link", { name: /Monitor profiles/ }));
    return { router, opener, drawer };
  }

  it("UXP-48 the drawer asks before leaving unsaved edits and stays open under the question", async () => {
    const { router, drawer } = await chooseMonitorWithAnEdit();
    const dialog = await screen.findByRole("alertdialog", { name: "Leave without saving?" });
    expect(dialog).toHaveTextContent("You have changes in Send policy that are not saved.");
    expect(router.state.location.pathname).toBe("/s/send_policy");
    expect(drawer).toBeInTheDocument();
  });

  it("UXP-48 staying keeps the section and the edit, and the drawer closes after the answer", async () => {
    const { router, opener } = await chooseMonitorWithAnEdit();
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Stay" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Sections menu" })).toBeNull();
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(router.state.location.pathname).toBe("/s/send_policy");
    expect(screen.getByLabelText("Name")).toHaveValue("send_policy-value!");
    expect(opener).toHaveFocus();
  });

  it("UXP-48 leaving opens the chosen section clean and the drawer closes after the answer", async () => {
    const { router } = await chooseMonitorWithAnEdit();
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard and leave" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/monitor");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Monitor profiles" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Sections menu" })).toBeNull();
    });
    expect(await screen.findByLabelText("Name")).toHaveValue("monitor-value");
  });

  it("UXP-48 choosing the section that is already open does not ask and closes the drawer", async () => {
    open("/s/send_policy");
    await userEvent.type(await screen.findByLabelText("Name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const drawer = await screen.findByRole("dialog", { name: "Sections menu" });
    await userEvent.click(within(drawer).getByRole("link", { name: /Send policy/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Sections menu" })).toBeNull();
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByLabelText("Name")).toHaveValue("send_policy-value!");
  });
});
