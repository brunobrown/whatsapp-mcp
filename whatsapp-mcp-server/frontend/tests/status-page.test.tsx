import { act, render, screen, within } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { queryClient } from "../src/lib/queryClient";
import { resetNoticeMemory } from "../src/lib/notices";
import { useUiStore } from "../src/stores/ui";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const sections = [
  { id: "status", title: "Status", icon: "activity", order: 0, kind: "page", description: "", restart: null },
];

const counters = {
  window_hours: 24,
  since: "2026-01-02T03:04:05Z",
  messages: { received: 1234, from_this_account: 5 },
  monitor_replies: { by_profile: { support: { sent: 2 } }, error_codes: { E1: 1 } },
  sends: { by_result: { allowed: 7 }, with_override: 1 },
  pending_drafts: 3,
  webhooks: { enabled: true, endpoints: 1, delivered: 9, failed: 0, dropped: 0, queued: 2 },
};

let viewport: ReturnType<typeof mockViewport>;

function openStatus() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/status"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

function serve(status: Record<string, unknown>, countersBody: Record<string, unknown> = counters) {
  server.use(
    http.get("*/api/ops/status", () => HttpResponse.json(status)),
    http.get("*/api/ops/counters", () => HttpResponse.json(countersBody)),
  );
}

beforeEach(() => {
  viewport = mockViewport(1280);
  resetNoticeMemory();
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(sections)),
  );
});
afterEach(() => {
  act(() => {
    toast.dismiss();
  });
  viewport.restore();
});

describe("status page", () => {
  it("says Connected in text, with uptime, when the bridge is connected", async () => {
    serve({ status: { connected: true, logged_in: true, uptime_seconds: 7500, version: "1.2.3" } });
    openStatus();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Paired with a phone")).toBeInTheDocument();
    expect(screen.getByText("2 h 5 min")).toBeInTheDocument();
    expect(screen.getByText("1.2.3")).toBeInTheDocument();
    expect(screen.queryByText("Disconnected")).not.toBeInTheDocument();
  });

  it("says Disconnected and Not paired when the bridge answers but is not connected", async () => {
    serve({ status: { connected: false, logged_in: false } });
    openStatus();
    expect(await screen.findByText("Disconnected")).toBeInTheDocument();
    expect(screen.getByText("Not paired")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
  });

  it.each([
    ["unreachable", "The bridge is not responding."],
    ["unauthorized", "The bridge refused the interface's access token. Restart the bridge so they use the same one."],
    ["unsupported", "This bridge does not offer that function. Update the bridge."],
    ["unexpected", "The bridge gave an answer that could not be understood."],
  ])("explains the reason %s in English", async (reason, sentence) => {
    serve({ unavailable: reason, message: "raw" }, { unavailable: reason, message: "raw" });
    openStatus();
    expect((await screen.findAllByText(sentence)).length).toBeGreaterThan(0);
    expect(screen.queryByText("raw")).not.toBeInTheDocument();
  });

  it("announces a bridge that does not answer once, as a notification, and stays quiet while it is connected", async () => {
    serve({ unavailable: "unreachable", message: "x" }, { unavailable: "unreachable", message: "x" });
    openStatus();
    await screen.findByText("Bridge status unavailable");
    expect(await screen.findAllByText("The bridge status is unavailable.")).toHaveLength(1);
  });

  it("raises no notification for a connected bridge", async () => {
    serve({ status: { connected: true } });
    openStatus();
    await screen.findByText("Connected");
    expect(screen.queryByText("The bridge status is unavailable.")).not.toBeInTheDocument();
  });

  it("shows the server's words for a reason it does not know", async () => {
    serve({ unavailable: "novel", message: "Something new happened." });
    openStatus();
    expect(await screen.findByText("Something new happened.")).toBeInTheDocument();
  });

  it("shows a dash for each count the bridge did not give", async () => {
    serve(
      { status: { connected: true } },
      { ...counters, messages: null, webhooks: null, pending_drafts: 0 },
    );
    openStatus();
    const received = (await screen.findByText("Messages received")).parentElement as HTMLElement;
    expect(within(received).getByText("—")).toBeInTheDocument();
    const delivered = screen.getByText("Webhooks delivered").parentElement as HTMLElement;
    expect(within(delivered).getByText("—")).toBeInTheDocument();
    const pending = screen.getByText("Pending drafts").parentElement as HTMLElement;
    expect(within(pending).getByText("0")).toBeInTheDocument();
  });

  it("shows counts formatted, and the tables scroll inside a focusable region", async () => {
    serve({ status: { connected: true } });
    openStatus();
    const received = (await screen.findByText("Messages received")).parentElement as HTMLElement;
    expect(within(received).getByText("1,234")).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "Sends by result" });
    expect(table).toHaveAttribute("tabindex", "0");
    expect(within(table).getByRole("cell", { name: "allowed" })).toBeInTheDocument();
  });

  it("has no heading of level 1 of its own (the shell header holds it)", async () => {
    serve({ status: { connected: true } });
    openStatus();
    await screen.findByText("Connected");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("UXP-17 Status cards: Bridge connection, Webhooks and Counters, in two columns from 1100 px", async () => {
    serve({ status: { connected: true, webhooks: { enabled: true, endpoints: [{ name: "crm" }] } } });
    openStatus();
    await screen.findByText("Connected");
    const connection = screen.getByRole("region", { name: "Bridge connection" });
    const hooks = screen.getByRole("region", { name: "Webhooks" });
    const count = screen.getByRole("region", { name: "Counters" });
    expect(within(hooks).getByText("On")).toBeInTheDocument();
    expect(within(hooks).getByRole("cell", { name: "crm" })).toBeInTheDocument();
    expect(within(connection).queryByText("On")).not.toBeInTheDocument();
    expect(within(count).getByText("Messages received")).toBeInTheDocument();
    const grid = connection.parentElement as HTMLElement;
    expect(grid).toContainElement(hooks);
    expect(grid).toContainElement(count);
    expect(grid.className).toContain("grid-cols-1");
    expect(grid.className).toContain("min-[1100px]:grid-cols-2");
  });

  it("UXP-37 counters in two columns under 600", async () => {
    serve({ status: { connected: true } });
    openStatus();
    const received = (await screen.findByText("Messages received")).parentElement as HTMLElement;
    const grid = received.parentElement as HTMLElement;
    expect(grid.tagName).toBe("DL");
    expect(grid.className).toMatch(/(^|\s)grid-cols-2(\s|$)/);
    expect(grid.className).not.toMatch(/(^|\s)(sm:)?grid-cols-1(\s|$)/);
  });
});
