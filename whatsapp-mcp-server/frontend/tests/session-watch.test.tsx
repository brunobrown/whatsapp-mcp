import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { api } from "../src/lib/api";
import * as notices from "../src/lib/notices";
import { queryClient } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const POLL = 15_000;
const sections = [
  { id: "send_policy", title: "Send policy", icon: "shield", order: 10, kind: "section", description: "", restart: null },
];
const IDLE = "Your session ended because of inactivity.";

let viewport: ReturnType<typeof mockViewport>;
let remaining: number[];
let polls: Request[];
let raise: MockInstance<typeof notices.raiseNotice>;

/** The session answers from `remaining`, one entry per poll; the last entry repeats. A null entry is a lapsed session. */
function serveSession() {
  let index = 0;
  server.use(
    http.get("*/api/session", ({ request }) => {
      polls.push(request);
      const next = remaining[Math.min(index, remaining.length - 1)];
      index += 1;
      if (next === undefined || next < 0) return HttpResponse.json({ state: "login", csrf: "n", message: IDLE });
      return HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: next });
    }),
  );
}

async function openShell() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/s/send_policy"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  await screen.findByRole("navigation", { name: "Sections" });
  return router;
}

async function tick(times: number) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL);
    });
  }
}

const warnings = () => raise.mock.calls.filter((c) => c[0] === "sessionWarning");

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  notices.resetNoticeMemory();
  raise = vi.spyOn(notices, "raiseNotice");
  polls = [];
  remaining = [900];
  server.use(http.get("*/api/sections", () => HttpResponse.json(sections)));
});
afterEach(() => {
  raise.mockRestore();
  viewport.restore();
  vi.useRealTimers();
});

describe("session watch", () => {
  it("polls the session in the background every 15 seconds without renewing it", async () => {
    serveSession();
    await openShell();
    const before = polls.length;
    await tick(2);
    expect(polls.length - before).toBe(2);
    expect(polls.at(-1)?.headers.get("x-background")).toBe("1");
  });

  it("warns once when 120 seconds or less remain, with the minutes left", async () => {
    remaining = [900, 300, 118, 103, 88, 73, 58];
    serveSession();
    await openShell();
    await tick(7);
    expect(warnings()).toHaveLength(1);
    expect(warnings()[0]).toEqual(["sessionWarning", { minutes: 2 }]);
  });

  it("does not warn above 120 seconds", async () => {
    remaining = [900, 600, 121];
    serveSession();
    await openShell();
    await tick(3);
    expect(warnings()).toHaveLength(0);
  });

  it("warns again after activity renews the session and it runs low once more", async () => {
    remaining = [900, 110, 100, 900, 800, 100];
    serveSession();
    await openShell();
    await tick(6);
    expect(warnings()).toHaveLength(2);
  });

  it("on a lapse clears the cache, raises the inactivity notice and shows it on the sign-in page", async () => {
    remaining = [900, 800, -1];
    serveSession();
    const router = await openShell();
    queryClient.setQueryData(["section", "send_policy"], { secret: "cached" });
    await tick(3);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(queryClient.getQueryData(["section", "send_policy"])).toBeUndefined();
    expect(raise.mock.calls.filter((c) => c[0] === "sessionEnded")).toHaveLength(1);
    expect(await screen.findByText(IDLE, { selector: "div" })).toBeInTheDocument();
  });

  it("treats a 401 with session_expired from any call as the same lapse", async () => {
    serveSession();
    const router = await openShell();
    server.use(http.get("*/api/x", () => HttpResponse.json({ code: "session_expired", message: IDLE }, { status: 401 })));
    await act(async () => {
      await api.get("/x").catch(() => undefined);
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(raise.mock.calls.filter((c) => c[0] === "sessionEnded")).toHaveLength(1);
  });

  it("does not raise the inactivity notice for a plain unauthenticated 401", async () => {
    serveSession();
    const router = await openShell();
    server.use(http.get("*/api/x", () => HttpResponse.json({ code: "unauthenticated", message: "No." }, { status: 401 })));
    await act(async () => {
      await api.get("/x").catch(() => undefined);
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(raise.mock.calls.filter((c) => c[0] === "sessionEnded")).toHaveLength(0);
  });

  it("clears the cache on sign-out and stops polling", async () => {
    serveSession();
    server.use(http.delete("*/api/session", () => HttpResponse.json({ ok: true })));
    const router = await openShell();
    queryClient.setQueryData(["section", "send_policy"], { secret: "cached" });
    server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "n" })));
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(queryClient.getQueryData(["section", "send_policy"])).toBeUndefined();
    const count = polls.length;
    await tick(3);
    expect(polls.length).toBe(count);
  });
});
