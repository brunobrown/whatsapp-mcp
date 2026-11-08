import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { queryClient } from "../src/lib/queryClient";
import { resetNoticeMemory } from "../src/lib/notices";
import { useUiStore } from "../src/stores/ui";
import { mockOverflow } from "./helpers/overflow";
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
let overflow: ReturnType<typeof mockOverflow> | undefined;

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
  overflow?.restore();
  overflow = undefined;
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
    overflow = mockOverflow();
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

  it("UXP-26 shows how long ago the status was read, and a Refresh button", async () => {
    serve({ status: { connected: true } });
    openStatus();
    expect(await screen.findByText(/^Updated \d+ s ago$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("UXP-27 Refresh fetches the status and the counters again at once and reads Updated 0 s ago", async () => {
    let reads = 0;
    serve({ status: { connected: true } });
    server.use(
      http.get("*/api/ops/status", () => {
        reads += 1;
        return HttpResponse.json({ status: { connected: true } });
      }),
    );
    openStatus();
    await screen.findByText("Connected");
    const before = reads;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      expect(reads).toBeGreaterThan(before);
    });
    expect(await screen.findByText("Updated 0 s ago")).toBeInTheDocument();
  });

  it("UXP-28 a failed fetch shows Could not update and keeps the previous status", async () => {
    queryClient.setDefaultOptions({ queries: { retry: false } });
    let fail = false;
    serve({ status: { connected: true } });
    server.use(
      http.get("*/api/ops/status", () =>
        fail ? HttpResponse.json({ code: "x", message: "Down." }, { status: 500 }) : HttpResponse.json({ status: { connected: true, version: "9.9.9" } }),
      ),
    );
    openStatus();
    await screen.findByText("9.9.9");
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Could not update")).toBeInTheDocument();
    expect(screen.getByText("9.9.9")).toBeInTheDocument();
    expect(screen.getByText(/^Updated \d+ s ago$/)).toBeInTheDocument();
  });

  it("UXP-30 every time is relative text with the exact UTC time in title and datetime", async () => {
    serve({ status: { connected: true, started_at: "2026-01-02T03:04:05Z" } });
    openStatus();
    const started = (await screen.findByText("Started")).parentElement as HTMLElement;
    const moment = started.querySelector("time") as HTMLElement;
    expect(moment.textContent).toMatch(/ ago$/);
    expect(moment).toHaveAttribute("datetime", "2026-01-02T03:04:05.000Z");
    expect(moment).toHaveAttribute("title", "2026-01-02 03:04:05 UTC");
    const window = (await screen.findByText(/^Counted since/)).querySelector("time") as HTMLElement;
    expect(window).toHaveAttribute("datetime", "2026-01-02T03:04:05.000Z");
  });
});
