import { act, render, screen, waitFor } from "@testing-library/react";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { api } from "../src/lib/api";
import { queryClient } from "../src/lib/queryClient";
import { server } from "./msw/server";

const sections = [
  { id: "monitor", title: "Monitor", icon: "eye", order: 20, kind: "section", description: "", restart: null },
  { id: "send_policy", title: "Send policy", icon: "send", order: 10, kind: "section", description: "", restart: null },
  { id: "status", title: "Status", icon: "activity", order: 0, kind: "page", description: "", restart: null },
];

function session(state: string) {
  server.use(http.get("*/api/session", () => HttpResponse.json({ state, csrf: "c", idle_remaining: 900 })));
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
  server.use(http.get("*/api/sections", () => HttpResponse.json(sections)));
});

describe("route guard", () => {
  it.each(["/", "/s/monitor", "/status", "/activity", "/account"])("sends an unauthenticated visit of %s to /login", async (path) => {
    session("login");
    const router = open(path);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
  });

  it("lets /login open for a visitor without a session", async () => {
    session("setup");
    const router = open("/login");
    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.location.pathname).toBe("/login");
  });

  it("sends a signed-in visitor away from /login", async () => {
    session("authenticated");
    const router = open("/login");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
  });
});

describe("routes", () => {
  it("/ goes to the first section by order, skipping pages", async () => {
    session("authenticated");
    const router = open("/");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
  });

  it.each(["/s/monitor", "/status", "/activity", "/account"])("opens %s for a signed-in visitor", async (path) => {
    session("authenticated");
    const router = open(path);
    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.location.pathname).toBe(path);
  });

  it("shows the not-found page with a link back to the settings", async () => {
    session("authenticated");
    open("/no/such/page");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the settings" })).toHaveAttribute("href", "/");
  });
});

describe("session loss", () => {
  it("goes to /login when any call answers 401", async () => {
    session("authenticated");
    const router = open("/s/monitor");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/monitor");
    });
    server.use(http.get("*/api/x", () => HttpResponse.json({ code: "session_expired", message: "Over." }, { status: 401 })));
    session("login");
    await act(async () => {
      await api.get("/x").catch(() => undefined);
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
  });
});
