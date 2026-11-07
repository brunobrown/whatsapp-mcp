// @vitest-environment node
import { describe, expect, it } from "vitest";
import config from "../vite.config";

describe("vite config", () => {
  it("proxies /api to the UI port", () => {
    const proxy = config.server?.proxy;
    expect(proxy).toBeDefined();
    const entry = proxy?.["/api"];
    expect(entry).toBeDefined();
    const target = typeof entry === "string" ? entry : entry?.target;
    expect(target).toBe("http://127.0.0.1:8765");
  });

  it("sets no CORS headers", () => {
    expect(config.server?.cors).toBeUndefined();
    expect(config.server?.headers).toBeUndefined();
  });

  it("builds into the directory the Python server serves", () => {
    expect(config.build?.outDir).toBe("../ui/web");
  });
});
