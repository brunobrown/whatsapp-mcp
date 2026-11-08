import { render, screen, within } from "@testing-library/react";
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
  { id: "activity", title: "Drafts and audit", icon: "history", order: 90, kind: "page", description: "", restart: null },
];

let viewport: ReturnType<typeof mockViewport>;

function openActivity() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/activity"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

function serve(options: { drafts?: object[]; send?: object[]; monitor?: object[] } = {}) {
  server.use(
    http.get("*/api/ops/drafts", () => HttpResponse.json({ rows: options.drafts ?? [], count: (options.drafts ?? []).length })),
    http.get("*/api/ops/audit/send", () => HttpResponse.json({ rows: options.send ?? [] })),
    http.get("*/api/ops/audit/monitor", () => HttpResponse.json({ rows: options.monitor ?? [] })),
  );
}

/** The first cell of each body row of a table, in the order drawn. */
function firstColumn(name: string): string[] {
  const region = screen.getByRole("region", { name });
  return within(region)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent ?? "");
}

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(sections)),
  );
});
afterEach(() => {
  viewport.restore();
  queryClient.setDefaultOptions({});
});

describe("activity page", () => {
  it("draws rows in the order the server sent them, without re-sorting", async () => {
    serve({
      drafts: [
        { id: "d-old", profile: "p", created_at: "2026-01-01T00:00:00Z" },
        { id: "d-new", profile: "p", created_at: "2026-03-01T00:00:00Z" },
      ],
      send: [
        { ts: "2026-02-01T10:00:00Z", result: "b" },
        { ts: "2026-05-01T10:00:00Z", result: "a" },
      ],
      monitor: [{ ts: "2026-02-01T10:00:00Z", profile: "z" }, { ts: "2026-05-01T10:00:00Z", profile: "a" }],
    });
    openActivity();
    await screen.findByRole("region", { name: "Pending drafts table" });
    expect(firstColumn("Pending drafts table")).toEqual(["d-old", "d-new"]);
    await screen.findByRole("region", { name: "Send audit table" });
    expect(firstColumn("Send audit table")).toEqual(["Feb 1, 2026, 10:00 UTC", "May 1, 2026, 10:00 UTC"]);
    await screen.findByRole("region", { name: "Monitor audit table" });
    expect(firstColumn("Monitor audit table")).toEqual(["Feb 1, 2026, 10:00 UTC", "May 1, 2026, 10:00 UTC"]);
  });

  it("shows the empty state of each list that has no rows", async () => {
    serve();
    openActivity();
    expect(await screen.findByText("No drafts are waiting.")).toBeInTheDocument();
    expect(screen.getByText("No sends are logged yet.")).toBeInTheDocument();
    expect(screen.getByText("No monitor replies are logged yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("offers nothing that sends, approves or discards: the page has no button at all", async () => {
    serve({ drafts: [{ id: "d1", profile: "p" }], send: [{ result: "ok" }], monitor: [{ profile: "p" }] });
    openActivity();
    await screen.findByRole("region", { name: "Monitor audit table" });
    const content = document.getElementById("content") as HTMLElement;
    expect(within(content).queryAllByRole("button")).toEqual([]);
    expect(within(content).queryAllByRole("checkbox")).toEqual([]);
    expect(within(content).queryAllByRole("link")).toEqual([]);
  });

  it("scrolls inside its own region: each table sits in a focusable, labelled region", async () => {
    serve({ send: [{ result: "ok" }] });
    openActivity();
    const region = await screen.findByRole("region", { name: "Send audit table" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toContain("overflow-x-auto");
  });

  it("shows a dash for a field a row lacks and Yes or No for flags", async () => {
    serve({ send: [{ override: true }, { override: false }] });
    openActivity();
    const region = await screen.findByRole("region", { name: "Send audit table" });
    const rows = within(region).getAllByRole("row").slice(1);
    expect(within(rows[0] as HTMLElement).getByText("Yes")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("No")).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getAllByText("—").length).toBeGreaterThan(3);
  });

  it("shows an error with a retry for a list that fails, and keeps the others", async () => {
    queryClient.setDefaultOptions({ queries: { retry: false } });
    serve({ drafts: [{ id: "d1" }] });
    server.use(http.get("*/api/ops/audit/send", () => HttpResponse.json({ code: "x", message: "Down." }, { status: 500 })));
    openActivity();
    expect(await screen.findByText("This list could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Pending drafts" })).toBeInTheDocument();
  });

  it("UXP-17 Activity cards: Pending drafts, Send audit and Monitor audit, each holding its own list", async () => {
    serve({ drafts: [{ id: "d1" }], send: [{ result: "ok" }], monitor: [{ profile: "p" }] });
    openActivity();
    await screen.findByRole("region", { name: "Pending drafts table" });
    for (const [card, table] of [
      ["Pending drafts", "Pending drafts table"],
      ["Send audit", "Send audit table"],
      ["Monitor audit", "Monitor audit table"],
    ] as const) {
      const region = screen.getByRole("region", { name: card });
      expect(within(region).getByRole("heading", { name: card })).toBeInTheDocument();
      expect(within(region).getByRole("region", { name: table })).toBeInTheDocument();
    }
  });
});
