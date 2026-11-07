import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { queryClient, SESSION_KEY } from "../src/lib/queryClient";
import {
  opsService,
  sectionsService,
  sessionService,
  useOpsStatus,
  useSection,
  useSections,
  useSession,
} from "../src/services/services";
import type { SessionInfo } from "../src/types/api";
import { server } from "./msw/server";

const signedIn: SessionInfo = { state: "authenticated", csrf: "tok-1", idle_remaining: 900 };

interface Seen {
  method: string;
  path: string;
  headers: Headers;
  body: unknown;
}

function record(): Seen[] {
  const seen: Seen[] = [];
  server.events.on("request:start", async ({ request }) => {
    const clone = request.clone();
    const text = await clone.text();
    seen.push({
      method: request.method,
      path: new URL(request.url).pathname,
      headers: request.headers,
      body: text ? (JSON.parse(text) as unknown) : null,
    });
  });
  return seen;
}

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function setVisibility(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
}

beforeEach(() => {
  queryClient.clear();
});
afterEach(() => {
  server.events.removeAllListeners();
  setVisibility("visible");
});

describe("sessionService", () => {
  it("reads the session and stores it so writes can send its csrf", async () => {
    server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "c0" })));
    const info = await sessionService.get();
    expect(info).toEqual({ state: "login", csrf: "c0" });
    expect(queryClient.getQueryData(SESSION_KEY)).toEqual(info);
  });

  it("sends the read as a background call when asked", async () => {
    const seen = record();
    server.use(http.get("*/api/session", () => HttpResponse.json(signedIn)));
    await sessionService.get({ background: true });
    expect(seen[0]?.headers.get("x-background")).toBe("1");
  });

  it("requests a code with POST /session/code", async () => {
    queryClient.setQueryData(SESSION_KEY, signedIn);
    const seen = record();
    server.use(http.post("*/api/session/code", () => HttpResponse.json({ announced: true })));
    expect(await sessionService.requestCode()).toEqual({ announced: true });
    expect(seen[0]).toMatchObject({ method: "POST", path: "/api/session/code" });
    expect(seen[0]?.headers.get("x-csrf-token")).toBe("tok-1");
  });

  it("sets up with code, password and confirm, and stores the new csrf", async () => {
    const seen = record();
    server.use(http.post("*/api/session/setup", () => HttpResponse.json({ ok: true, csrf: "fresh" })));
    const result = await sessionService.setup({ code: "123", password: "p", confirm: "p" });
    expect(result.ok).toBe(true);
    expect(seen[0]).toMatchObject({ method: "POST", path: "/api/session/setup", body: { code: "123", password: "p", confirm: "p" } });
    expect(queryClient.getQueryData<SessionInfo>(SESSION_KEY)).toMatchObject({ state: "authenticated", csrf: "fresh" });
  });

  it("logs in with the password and stores the new csrf", async () => {
    const seen = record();
    server.use(http.post("*/api/session/login", () => HttpResponse.json({ ok: true, csrf: "after" })));
    await sessionService.login({ password: "secret" });
    expect(seen[0]).toMatchObject({ method: "POST", path: "/api/session/login", body: { password: "secret" } });
    expect(queryClient.getQueryData<SessionInfo>(SESSION_KEY)?.csrf).toBe("after");
  });

  it("keeps the cache untouched on a refused login", async () => {
    server.use(http.post("*/api/session/login", () => HttpResponse.json({ ok: false, message: "No." })));
    const result = await sessionService.login({ password: "x" });
    expect(result).toEqual({ ok: false, message: "No." });
    expect(queryClient.getQueryData(SESSION_KEY)).toBeUndefined();
  });

  it("signs out with DELETE /session and clears the cache", async () => {
    queryClient.setQueryData(SESSION_KEY, signedIn);
    queryClient.setQueryData(["section", "channel"], { x: 1 });
    const seen = record();
    server.use(http.delete("*/api/session", () => HttpResponse.json({ ok: true })));
    await sessionService.logout();
    expect(seen[0]).toMatchObject({ method: "DELETE", path: "/api/session" });
    expect(queryClient.getQueryData(["section", "channel"])).toBeUndefined();
    expect(queryClient.getQueryData(SESSION_KEY)).toBeUndefined();
  });

  it("changes the password with current, new and confirm", async () => {
    queryClient.setQueryData(SESSION_KEY, signedIn);
    const seen = record();
    server.use(http.post("*/api/account/password", () => HttpResponse.json({ ok: true })));
    await sessionService.changePassword({ current: "a", new: "b", confirm: "b" });
    expect(seen[0]).toMatchObject({ method: "POST", path: "/api/account/password", body: { current: "a", new: "b", confirm: "b" } });
  });
});

describe("sectionsService", () => {
  beforeEach(() => {
    queryClient.setQueryData(SESSION_KEY, signedIn);
  });

  it("lists sections and reads one by id", async () => {
    const seen = record();
    server.use(
      http.get("*/api/sections", () => HttpResponse.json([{ id: "channel" }])),
      http.get("*/api/sections/channel", () => HttpResponse.json({ id: "channel" })),
    );
    expect(await sectionsService.list()).toEqual([{ id: "channel" }]);
    expect(await sectionsService.get("channel")).toEqual({ id: "channel" });
    expect(seen.map((s) => `${s.method} ${s.path}`)).toEqual(["GET /api/sections", "GET /api/sections/channel"]);
  });

  it("validates with POST .../validate and the draft", async () => {
    const seen = record();
    server.use(http.post("*/api/sections/channel/validate", () => HttpResponse.json({ errors: {}, general: [], can_save: true })));
    const body = { version: "v1", changes: { port: "80" }, restore: [] };
    expect((await sectionsService.validate("channel", body)).can_save).toBe(true);
    expect(seen[0]).toMatchObject({ method: "POST", path: "/api/sections/channel/validate", body });
  });

  it("saves with PUT and returns the outcome", async () => {
    const seen = record();
    const outcome = { ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: ["bridge"] };
    server.use(http.put("*/api/sections/channel", () => HttpResponse.json(outcome)));
    const body = { version: "v1", changes: { a: true }, restore: ["b"], confirm_replace: true };
    expect(await sectionsService.save("channel", body)).toEqual(outcome);
    expect(seen[0]).toMatchObject({ method: "PUT", path: "/api/sections/channel", body });
    expect(seen[0]?.headers.get("x-csrf-token")).toBe("tok-1");
  });

  it.each([
    [409, { ok: false, conflict: true }],
    [409, { ok: false, needs_confirmation: true }],
    [422, { ok: false, errors: { port: "Bad." }, general: [] }],
  ])("returns the outcome body of a %i instead of throwing", async (status, body) => {
    server.use(http.put("*/api/sections/channel", () => HttpResponse.json(body, { status })));
    expect(await sectionsService.save("channel", { version: "v", changes: {} })).toMatchObject(body);
  });

  it("still throws on other failures", async () => {
    server.use(http.put("*/api/sections/channel", () => HttpResponse.json({ code: "csrf", message: "x" }, { status: 403 })));
    await expect(sectionsService.save("channel", { version: "v", changes: {} })).rejects.toMatchObject({ status: 403 });
  });
});

describe("opsService", () => {
  beforeEach(() => {
    queryClient.setQueryData(SESSION_KEY, signedIn);
  });

  it("reads each ops path and marks every read as background", async () => {
    const seen = record();
    server.use(
      http.get("*/api/ops/status", () => HttpResponse.json({ status: {} })),
      http.get("*/api/ops/counters", () => HttpResponse.json({})),
      http.get("*/api/ops/drafts", () => HttpResponse.json({ rows: [], count: 0 })),
      http.get("*/api/ops/audit/send", () => HttpResponse.json({ rows: [] })),
      http.get("*/api/ops/audit/monitor", () => HttpResponse.json({ rows: [] })),
    );
    await opsService.status();
    await opsService.counters();
    await opsService.drafts();
    await opsService.audit("send");
    await opsService.audit("monitor");
    expect(seen.map((s) => s.path)).toEqual([
      "/api/ops/status",
      "/api/ops/counters",
      "/api/ops/drafts",
      "/api/ops/audit/send",
      "/api/ops/audit/monitor",
    ]);
    expect(seen.every((s) => s.headers.get("x-background") === "1")).toBe(true);
  });
});

describe("query hooks", () => {
  beforeEach(() => {
    queryClient.setQueryData(SESSION_KEY, signedIn);
  });

  it("session and sections hooks load their data", async () => {
    server.use(
      http.get("*/api/session", () => HttpResponse.json(signedIn)),
      http.get("*/api/sections", () => HttpResponse.json([{ id: "a" }])),
    );
    const session = renderHook(() => useSession(), { wrapper });
    const sections = renderHook(() => useSections(), { wrapper });
    await waitFor(() => {
      expect(session.result.current.data).toEqual(signedIn);
    });
    await waitFor(() => {
      expect(sections.result.current.data).toEqual([{ id: "a" }]);
    });
  });

  it("a section read refetches when the window regains focus", async () => {
    let reads = 0;
    server.use(
      http.get("*/api/sections/channel", () => {
        reads += 1;
        return HttpResponse.json({ id: "channel" });
      }),
    );
    const { result } = renderHook(() => useSection("channel"), { wrapper });
    await waitFor(() => {
      expect(result.current.data).toBeDefined();
    });
    expect(reads).toBe(1);
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => {
      expect(reads).toBe(2);
    });
  });

  it("an ops read polls every 5 s while the tab is visible and stops while hidden", () => {
    server.use(http.get("*/api/ops/status", () => HttpResponse.json({ status: {} })));
    const poll = (): unknown => {
      const { result } = renderHook(() => useOpsStatus(), { wrapper });
      return result.current;
    };
    poll();
    const query = queryClient.getQueryCache().find({ queryKey: ["ops", "status"] });
    const options = query?.options as unknown as { refetchInterval?: () => number | false } | undefined;
    const interval = options?.refetchInterval;
    expect(interval).toBeTypeOf("function");
    setVisibility("visible");
    expect(interval?.()).toBe(5000);
    setVisibility("hidden");
    expect(interval?.()).toBe(false);
  });
});
