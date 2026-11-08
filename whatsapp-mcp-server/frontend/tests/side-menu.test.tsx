import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { describe, expect, it } from "vitest";
import { SideMenu } from "../src/features/menu/SideMenu";
import type { SideMenuMode } from "../src/features/menu/SideMenu";
import type { SectionSummary } from "../src/types/api";

function entry(id: string, title: string, description = "", kind: "section" | "page" = "section"): SectionSummary {
  return { id, title, icon: "shield", order: 0, kind, description, restart: null, pending: false };
}

const FEW = [
  entry("status", "Status", "Bridge and counters", "page"),
  entry("send_policy", "Send policy", "Who may send"),
  entry("media", "Media downloads", "Where files go"),
  entry("account", "Account", "Password", "page"),
];
const MANY = [
  ...FEW,
  entry("monitor", "Monitor profiles", "Watched chats"),
  entry("webhooks", "Webhooks", "Outgoing calls"),
  entry("channel", "Channel", "Reply channel"),
  entry("privacy", "Privacy", "What is kept"),
  entry("timezone", "Timezone", "Clock"),
];

async function show(entries: SectionSummary[], mode: SideMenuMode, pending: string[] = [], path = "/s/media") {
  cleanup();
  const router = createRouter({
    routeTree: createRootRoute({
      component: () => <SideMenu entries={entries} mode={mode} isPending={(e) => pending.includes(e.id)} />,
    }),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
  return screen.findByRole("navigation", { name: "Sections" });
}

describe("SideMenu expanded", () => {
  it("UXP-05 shows the sections under headings in the fixed order and omits an empty group", async () => {
    const nav = await show([...FEW, entry("strange", "Strange")], "expanded");
    expect(within(nav).getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Overview",
      "Messaging",
      "Safety and data",
      "Preferences",
      "More",
    ]);
    const only = await show([entry("status", "Status")], "expanded");
    expect(within(only).getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Overview"]);
  });

  it("UXP-07 titles are cut with a full title attribute", async () => {
    const long = "A very long section title that cannot fit in three hundred and twenty pixels of menu";
    const longer = "A description that is just as long and gets cut with an ellipsis after one single line of text";
    const nav = await show([entry("media", long, longer)], "expanded");
    const title = within(nav).getByText(long);
    const description = within(nav).getByText(longer);
    expect(title).toHaveAttribute("title", long);
    expect(title).toHaveClass("truncate");
    expect(description).toHaveAttribute("title", longer);
    expect(description).toHaveClass("truncate");
  });

  it("marks the open section as the current page and shows the Restart needed badge only where it waits", async () => {
    const nav = await show(FEW, "expanded", ["media"]);
    expect(within(nav).getByRole("link", { name: /Media downloads/ })).toHaveAttribute("aria-current", "page");
    expect(within(within(nav).getByRole("link", { name: /Media downloads/ })).getByText("Restart needed")).toBeInTheDocument();
    expect(within(within(nav).getByRole("link", { name: /Send policy/ })).queryByText("Restart needed")).toBeNull();
  });

  it("UXP-06 the filter field appears above 8 entries and not at 8", async () => {
    const eight = await show(MANY.slice(0, 8), "expanded");
    expect(within(eight).queryByRole("searchbox", { name: "Filter sections" })).toBeNull();
    const nine = await show(MANY, "expanded");
    expect(within(nine).getByRole("searchbox", { name: "Filter sections" })).toBeInTheDocument();
  });

  it("UXP-06 filter hides entries that do not match, ignoring case, in title or description", async () => {
    const nav = await show(MANY, "expanded");
    await userEvent.type(within(nav).getByRole("searchbox", { name: "Filter sections" }), "WHERE FILES");
    expect(within(nav).getAllByRole("link").map((l) => l.textContent)).toEqual([expect.stringContaining("Media downloads")]);
  });

  it("UXP-06 filter empty state says No section matches and Clear filter brings every entry back", async () => {
    const nav = await show(MANY, "expanded");
    const field = within(nav).getByRole("searchbox", { name: "Filter sections" });
    await userEvent.type(field, "zzzz");
    expect(within(nav).getByText("No section matches")).toBeInTheDocument();
    expect(within(nav).queryAllByRole("link")).toHaveLength(0);
    await userEvent.click(within(nav).getByRole("button", { name: "Clear filter" }));
    expect(within(nav).getAllByRole("link")).toHaveLength(MANY.length);
    expect(field).toHaveValue("");
  });
});

describe("SideMenu rail", () => {
  it("UXP-02 rail entries have names, tooltips and the restart state", async () => {
    const nav = await show(FEW, "rail", ["media"]);
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("aria-label"))).toEqual([
      "Status",
      "Send policy",
      "Media downloads: Restart needed",
      "Account",
    ]);
    // No visible text in a rail entry: the icon is the whole entry.
    expect(links.every((l) => l.textContent === "")).toBe(true);
    // The dot sits on the icon of the entry that waits for a restart, and only there.
    expect(links.map((l) => l.querySelector("[data-dot=restart]") !== null)).toEqual([false, false, true, false]);

    await userEvent.hover(links[1] as HTMLElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Send policy");
  });

  it("UXP-02 a rail entry shows its tooltip on keyboard focus", async () => {
    const nav = await show(FEW, "rail", ["media"]);
    await userEvent.tab();
    expect(within(nav).getByRole("link", { name: "Status" })).toHaveFocus();
    await userEvent.tab();
    await userEvent.tab();
    expect(within(nav).getByRole("link", { name: "Media downloads: Restart needed" })).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Media downloads: Restart needed");
  });

  it("UXP-02 the rail shows no filter field and no description even with more than 8 entries", async () => {
    const nav = await show(MANY, "rail");
    expect(within(nav).queryByRole("searchbox")).toBeNull();
    expect(within(nav).getAllByRole("link")).toHaveLength(MANY.length);
    expect(within(nav).queryByText("Who may send")).toBeNull();
  });
});
