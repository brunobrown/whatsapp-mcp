import { http, HttpResponse, delay } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, isApiError, onUnauthorized } from "../src/lib/api";
import { queryClient, SESSION_KEY } from "../src/lib/queryClient";
import type { SessionInfo } from "../src/types/api";
import { server } from "./msw/server";

const signedIn: SessionInfo = { state: "authenticated", csrf: "tok-123", idle_remaining: 900 };

function captureHeaders(method: "get" | "post" | "put" | "delete", path: string) {
  const seen: { headers: Headers | null; body: unknown } = { headers: null, body: null };
  server.use(
    http[method](path, async ({ request }) => {
      seen.headers = request.headers;
      seen.body = method === "get" || method === "delete" ? null : await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  return seen;
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(SESSION_KEY, signedIn);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("api client", () => {
  it("uses /api, a 10 second timeout and sends cookies", () => {
    expect(api.defaults.baseURL).toBe("/api");
    expect(api.defaults.timeout).toBe(10_000);
    expect(api.defaults.withCredentials).toBe(true);
  });

  it("attaches the CSRF header to POST, PUT and DELETE", async () => {
    const post = captureHeaders("post", "*/api/a");
    const put = captureHeaders("put", "*/api/b");
    const del = captureHeaders("delete", "*/api/c");
    await api.post("/a", { x: 1 });
    await api.put("/b", { x: 2 });
    await api.delete("/c");
    expect(post.headers?.get("x-csrf-token")).toBe("tok-123");
    expect(put.headers?.get("x-csrf-token")).toBe("tok-123");
    expect(del.headers?.get("x-csrf-token")).toBe("tok-123");
  });

  it("does not attach the CSRF header to reads", async () => {
    const read = captureHeaders("get", "*/api/r");
    await api.get("/r");
    expect(read.headers?.get("x-csrf-token")).toBeNull();
  });

  it("sends a JSON body with the JSON content type on writes", async () => {
    const post = captureHeaders("post", "*/api/a");
    await api.post("/a", { password: "p" });
    expect(post.headers?.get("content-type")).toContain("application/json");
    expect(post.body).toEqual({ password: "p" });
  });

  it("uses the token the session cache holds at the moment of the call", async () => {
    const post = captureHeaders("post", "*/api/a");
    queryClient.setQueryData(SESSION_KEY, { ...signedIn, csrf: "tok-new" });
    await api.post("/a", {});
    expect(post.headers?.get("x-csrf-token")).toBe("tok-new");
  });

  it("sends no CSRF header when no session is cached", async () => {
    queryClient.clear();
    const post = captureHeaders("post", "*/api/a");
    await api.post("/a", {});
    expect(post.headers?.get("x-csrf-token")).toBeNull();
  });
});

describe("401 handling", () => {
  it.each(["unauthenticated", "session_expired"])("clears the cache and signals sign-in on %s", async (code) => {
    queryClient.setQueryData(["section", "channel"], { secret: "cached" });
    server.use(
      http.get("*/api/sections", () => HttpResponse.json({ code, message: "Sign in first." }, { status: 401 })),
    );
    const heard: ApiError[] = [];
    const stop = onUnauthorized((error) => heard.push(error));

    await expect(api.get("/sections")).rejects.toMatchObject({ status: 401, code, message: "Sign in first." });

    stop();
    expect(heard).toHaveLength(1);
    expect(heard[0]?.code).toBe(code);
    expect(queryClient.getQueryData(SESSION_KEY)).toBeUndefined();
    expect(queryClient.getQueryData(["section", "channel"])).toBeUndefined();
  });

  it("stops signalling after the listener is removed", async () => {
    server.use(
      http.get("*/api/sections", () => HttpResponse.json({ code: "unauthenticated", message: "x" }, { status: 401 })),
    );
    const listener = vi.fn();
    onUnauthorized(listener)();
    await expect(api.get("/sections")).rejects.toBeInstanceOf(ApiError);
    expect(listener).not.toHaveBeenCalled();
  });
});

describe("error normalisation", () => {
  it("carries wait on a 429", async () => {
    server.use(
      http.post("*/api/session/login", () =>
        HttpResponse.json({ ok: false, message: "Too many attempts.", wait: 8 }, { status: 429 }),
      ),
    );
    const error = await api.post("/session/login", { password: "x" }).catch((e: unknown) => e);
    expect(isApiError(error)).toBe(true);
    expect(error).toMatchObject({ status: 429, code: "throttled", wait: 8, message: "Too many attempts." });
  });

  it("carries the field errors on a 422 and the raw body", async () => {
    const body = { ok: false, errors: { port: "Must be a whole number." }, general: ["Fix the values."] };
    server.use(http.put("*/api/sections/x", () => HttpResponse.json(body, { status: 422 })));
    const error = await api.put("/sections/x", {}).catch((e: unknown) => e);
    expect(error).toMatchObject({
      status: 422,
      code: "invalid",
      fields: { port: "Must be a whole number." },
      message: "Fix the values.",
      data: body,
    });
  });

  it("tells a conflict from a confirmation request on a 409", async () => {
    server.use(
      http.put("*/api/sections/a", () => HttpResponse.json({ ok: false, conflict: true }, { status: 409 })),
      http.put("*/api/sections/b", () =>
        HttpResponse.json({ ok: false, needs_confirmation: true }, { status: 409 }),
      ),
    );
    await expect(api.put("/sections/a", {})).rejects.toMatchObject({ status: 409, code: "conflict" });
    await expect(api.put("/sections/b", {})).rejects.toMatchObject({ status: 409, code: "needs_confirmation" });
  });

  it.each([
    [421, "bad_host"],
    [403, "origin"],
    [403, "csrf"],
    [403, "content_type"],
    [404, "not_found"],
    [400, "bad_request"],
  ])("keeps the server code and message for %i %s", async (status, code) => {
    server.use(http.get("*/api/x", () => HttpResponse.json({ code, message: `Refused: ${code}` }, { status })));
    await expect(api.get("/x")).rejects.toMatchObject({ status, code, message: `Refused: ${code}` });
  });

  it("types a network failure and gives it the catalog text", async () => {
    server.use(http.get("*/api/x", () => HttpResponse.error()));
    const error = await api.get("/x").catch((e: unknown) => e);
    expect(error).toMatchObject({
      status: 0,
      code: "network",
      message: "The interface lost its connection.",
    });
  });

  it("types a timeout", async () => {
    server.use(
      http.get("*/api/slow", async () => {
        await delay("infinite");
        return HttpResponse.json({});
      }),
    );
    const error = await api.get("/slow", { timeout: 50 }).catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 0, code: "timeout", message: "The request took too long." });
  });

  it("types a non-JSON failure as unexpected and keeps the status", async () => {
    server.use(http.get("*/api/x", () => new HttpResponse("<html>Bad gateway</html>", { status: 502 })));
    await expect(api.get("/x")).rejects.toMatchObject({
      status: 502,
      code: "unexpected",
      message: "Something went wrong. Try again.",
    });
  });

  it("returns a refused sign-in (HTTP 200, ok false) as data, not as an error", async () => {
    server.use(http.post("*/api/session/login", () => HttpResponse.json({ ok: false, message: "Wrong password." })));
    const response = await api.post<{ ok: boolean; message: string }>("/session/login", { password: "x" });
    expect(response.data).toEqual({ ok: false, message: "Wrong password." });
  });
});
