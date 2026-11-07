// @vitest-environment node
import http from "node:http";
import type { AddressInfo } from "node:net";
import { createServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { server } from "./msw/server";
import config, { apiProxy } from "../vite.config";

interface Seen {
  host?: string;
  origin?: string;
}

async function throughTheDevServer(port: number, headers: Record<string, string>): Promise<Seen> {
  let seen: Seen = {};
  const api = http.createServer((req, res) => {
    seen = { host: req.headers.host, origin: req.headers.origin };
    res.end("{}");
  });
  await new Promise<void>((ok) => api.listen(port, "127.0.0.1", ok));
  const dev = await createServer({
    configFile: false,
    logLevel: "silent",
    server: { port: 0, host: "127.0.0.1", proxy: { "/api": apiProxy(String(port)) } },
  });
  await dev.listen();
  try {
    const vitePort = (dev.httpServer?.address() as AddressInfo).port;
    await new Promise<void>((ok, fail) => {
      const req = http.request({ host: "localhost", port: vitePort, path: "/api/session", method: "POST", headers }, (res) => {
        res.resume();
        res.on("end", ok);
      });
      req.on("error", fail);
      req.end();
    });
    return seen;
  } finally {
    await dev.close();
    await new Promise<void>((ok) => {
      api.close(() => {
        ok();
      });
    });
  }
}

describe("vite config", () => {
  // The real dev server and a real local API are used here: the browser mock must not intercept them.
  beforeAll(() => {
    server.close();
  });
  afterAll(() => {
    // The shared teardown closes the mock once more.
    server.listen({ onUnhandledFrame: "error" });
  });

  it("proxies /api to the UI port", () => {
    const entry = config.server?.proxy?.["/api"];
    expect(entry).toBeDefined();
    const target = typeof entry === "string" ? entry : entry?.target;
    expect(target).toBe("http://127.0.0.1:8765");
  });

  it("follows WHATSAPP_UI_PORT", () => {
    expect(apiProxy("9123").target).toBe("http://127.0.0.1:9123");
  });

  it("forwards the Host and Origin of the API, not the ones of the Vite server (non-default port)", async () => {
    const port = 18000 + Math.floor(Math.random() * 1000);
    const seen = await throughTheDevServer(port, { origin: "http://localhost:5173" });
    expect(seen.host).toBe(`127.0.0.1:${port}`);
    expect(seen.origin).toBe(`http://127.0.0.1:${port}`);
  });

  it("does not invent an Origin for a request that sent none", async () => {
    const port = 19000 + Math.floor(Math.random() * 1000);
    const seen = await throughTheDevServer(port, {});
    expect(seen.host).toBe(`127.0.0.1:${port}`);
    expect(seen.origin).toBeUndefined();
  });

  it("sets no CORS headers", () => {
    expect(config.server?.cors).toBeUndefined();
    expect(config.server?.headers).toBeUndefined();
  });

  it("builds into the directory the Python server serves", () => {
    expect(config.build?.outDir).toBe("../ui/web");
  });
});
