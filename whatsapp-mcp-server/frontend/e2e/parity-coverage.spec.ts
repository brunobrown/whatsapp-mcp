import { existsSync, readdirSync, readFileSync } from "node:fs";
import { createConnection, createServer } from "node:net";
import { networkInterfaces } from "node:os";
import type { AddressInfo } from "node:net";
import { join, resolve } from "node:path";
import { request as httpRequest } from "node:http";
import ts from "typescript";
import { expect, harnessForFile, SERVER_DIR, test } from "./support/fixtures";
import { freePort } from "./support/harness";
import { end, listening, runUi, waitForUrl, waitUntilServing } from "./support/process";

// UI-01..UI-07 of the interface-grafica spec, and the test that keeps every requirement covered.
//
// The bridge's own part of UI-01, UI-02, UI-03 and UI-07 (starting the interface, stopping it with the bridge,
// carrying on when it fails, not restarting it) is the Go supervisor's and is proved by the Go tests that
// parity-ids.json names. Each test here asserts what the interface process itself does, which the bridge relies on.

const REPO = resolve(SERVER_DIR, "..");
const E2E = resolve(SERVER_DIR, "frontend", "e2e");
const harness = harnessForFile();

interface Requirement {
  id: string;
  summary: string;
  go?: string[];
}
const manifest = JSON.parse(readFileSync(join(E2E, "parity-ids.json"), "utf8")) as { requirements: Requirement[] };

/** The titles of the tests of every spec file in e2e/, read from the source with the TypeScript parser. */
function testTitles(): string[] {
  const titles: string[] = [];
  for (const name of readdirSync(E2E).filter((entry) => entry.endsWith(".spec.ts"))) {
    const file = ts.createSourceFile(name, readFileSync(join(E2E, name), "utf8"), ts.ScriptTarget.Latest, true);
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "test") {
        const [first] = node.arguments;
        if (first !== undefined && (ts.isStringLiteral(first) || ts.isNoSubstitutionTemplateLiteral(first))) titles.push(first.text);
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return titles;
}

/** The IDs with no test whose title starts with them. */
export function idsWithoutATest(ids: readonly string[], titles: readonly string[]): string[] {
  return ids.filter((id) => !titles.some((title) => title === id || title.startsWith(`${id} `)));
}

async function refused(env: Record<string, string>): Promise<{ code: string; text: string }> {
  const run = runUi(env);
  try {
    const code = await run.exited;
    return { code, text: run.text() };
  } finally {
    await end(run);
  }
}

test("UI-01 the interface prints the address the bridge logs and answers there with the pages and the API", async () => {
  const { output, url } = harness.current;
  expect(output.some((line) => line === `ui: WhatsApp MCP interface at ${url}`)).toBe(true);
  const page = await fetch(`${url}/`);
  expect(page.headers.get("content-type")).toContain("text/html");
  expect(await page.text()).toContain('<html lang="en">');
  expect((await fetch(`${url}/api/session`)).ok).toBe(true);
  // The command the bridge runs by default is this process: `python -m ui`.
  const supervisor = readFileSync(join(REPO, "whatsapp-bridge", "uisupervisor.go"), "utf8");
  expect(/uiDefaultCommand = "uv run --directory \.\.\/whatsapp-mcp-server python -m ui"/.test(supervisor)).toBe(true);
});

test("UI-02 on the bridge's request to end (SIGTERM) the interface stops cleanly, frees its port and leaves nothing behind", async () => {
  const port = await freePort();
  const run = runUi({ WHATSAPP_UI_PORT: String(port) });
  try {
    const url = await waitForUrl(run);
    await waitUntilServing(url, run);
    const pid = run.child.pid ?? 0;
    run.child.kill("SIGTERM");
    expect(await run.exited).toBe("0");
    expect(await listening(url)).toBe(false);
    expect(() => process.kill(pid, 0)).toThrow(/ESRCH/);
  } finally {
    await end(run);
  }
});

test("UI-03 a busy port is reported with its reason and the process ends, so the bridge can go on without it", async () => {
  const holder = createServer();
  await new Promise<void>((done) => holder.listen(0, "127.0.0.1", done));
  const port = (holder.address() as AddressInfo).port;
  try {
    const result = await refused({ WHATSAPP_UI_PORT: String(port) });
    expect(result.code).toBe("1");
    expect(result.text).toContain(`port ${String(port)} on 127.0.0.1 is already in use`);
    expect(result.text).toContain("change WHATSAPP_UI_PORT");
    expect(result.text).not.toContain("Traceback");
  } finally {
    await new Promise((done) => holder.close(done));
  }
});

test("UI-04 a port from 1024 to 65535 is listened on, from the environment or the saved settings; any other value is refused", async () => {
  const message = "WHATSAPP_UI_PORT must be a whole number from 1024 to 65535";
  for (const bad of ["1023", "80", "0", "65536", "70000", "-5", "abc", "8765.5", "1e3"]) {
    const result = await refused({ WHATSAPP_UI_PORT: bad });
    expect(result.code, bad).toBe("1");
    expect(result.text, bad).toContain(message);
    expect(result.text, bad).toContain(`(got '${bad}')`);
  }
  const fromEnvironment = await freePort();
  const run = runUi({ WHATSAPP_UI_PORT: String(fromEnvironment) });
  try {
    expect(await waitForUrl(run)).toBe(`http://127.0.0.1:${String(fromEnvironment)}`);
  } finally {
    await end(run);
  }
  // The same port, saved by the interface itself and with no variable set.
  const saved = await freePort();
  const second = runUi({}, { "ui_settings.json": JSON.stringify({ version: 1, env: { WHATSAPP_UI_PORT: String(saved) } }) });
  try {
    expect(await waitForUrl(second)).toBe(`http://127.0.0.1:${String(saved)}`);
  } finally {
    await end(second);
  }
});

test("UI-05 by default the interface listens on 127.0.0.1 only", async () => {
  const port = await freePort();
  const run = runUi({ WHATSAPP_UI_PORT: String(port) });
  try {
    const url = await waitForUrl(run);
    expect(url).toBe(`http://127.0.0.1:${String(port)}`);
    await waitUntilServing(url, run);
    const reaches = (host: string): Promise<boolean> =>
      new Promise((done) => {
        const socket = createConnection({ host, port, timeout: 2000 });
        socket.once("connect", () => {
          socket.destroy();
          done(true);
        });
        socket.once("error", () => {
          done(false);
        });
        socket.once("timeout", () => {
          socket.destroy();
          done(false);
        });
      });
    expect(await reaches("127.0.0.1")).toBe(true);
    // Another address of this machine, loopback range or not, is not where it listens.
    const others = Object.values(networkInterfaces())
      .flatMap((list) => list ?? [])
      .filter((address) => address.family === "IPv4" && !address.internal)
      .map((address) => address.address);
    if (process.platform === "linux") others.push("127.0.0.2");
    expect(others.length).toBeGreaterThan(0);
    for (const host of others) expect(await reaches(host), host).toBe(false);
  } finally {
    await end(run);
  }
});

test("UI-06 a host outside loopback is refused without WHATSAPP_UI_ALLOW_REMOTE=true and allowed, with a warning, with it", async () => {
  const port = await freePort();
  for (const host of ["0.0.0.0", "192.0.2.10", "example.com"]) {
    const result = await refused({ WHATSAPP_UI_HOST: host, WHATSAPP_UI_PORT: String(port) });
    expect(result.code, host).toBe("1");
    expect(result.text, host).toContain(`WHATSAPP_UI_HOST=${host} is not a loopback address`);
    expect(result.text, host).toContain("WHATSAPP_UI_ALLOW_REMOTE=true");
    expect(await listening(`http://127.0.0.1:${String(port)}`), host).toBe(false);
  }
  const run = runUi({ WHATSAPP_UI_HOST: "0.0.0.0", WHATSAPP_UI_ALLOW_REMOTE: "true", WHATSAPP_UI_PORT: String(port) });
  try {
    const url = await waitForUrl(run);
    expect(url).toBe(`http://0.0.0.0:${String(port)}`);
    await waitUntilServing(`http://127.0.0.1:${String(port)}`, run);
    expect(run.text()).toContain("warning: The interface listens on 0.0.0.0");
    expect(run.text()).toContain("no TLS");
    // Allowed to listen is not allowed to answer to any name: a foreign Host is refused.
    const status = await new Promise<number>((done, fail) => {
      const call = httpRequest({ host: "127.0.0.1", port, path: "/api/session", headers: { Host: `evil.example:${String(port)}` } }, (response) => {
        response.resume();
        done(response.statusCode ?? 0);
      });
      call.once("error", fail);
      call.end();
    });
    expect(status).toBe(421);
  } finally {
    await end(run);
  }
});

test("UI-07 an interface process that dies stays dead on its own: it never restarts itself", async () => {
  // Whether the bridge restarts it is the bridge's rule and is proved by the Go test TestUIExitIsLoggedAndNotRestarted
  // (parity-ids.json). From the process side the claim is that nothing of the interface comes back by itself.
  const port = await freePort();
  const run = runUi({ WHATSAPP_UI_PORT: String(port) });
  try {
    const url = await waitForUrl(run);
    await waitUntilServing(url, run);
    run.child.kill("SIGKILL");
    expect(await run.exited).toBe("SIGKILL");
    // Give a self-restarting wrapper time to show itself; a quiet port and no new output is the proof.
    await new Promise((done) => setTimeout(done, 2500));
    expect(await listening(url)).toBe(false);
    expect(run.output.filter((line) => line.includes("interface at "))).toHaveLength(1);
  } finally {
    await end(run);
  }
});

test("every requirement of interface-grafica has a parity test", () => {
  const ids = manifest.requirements.map((requirement) => requirement.id);
  expect(ids).toHaveLength(38);
  expect(new Set(ids).size).toBe(ids.length);
  expect(manifest.requirements.every((requirement) => requirement.summary.length > 10)).toBe(true);
  const titles = testTitles();
  expect(idsWithoutATest(ids, titles)).toEqual([]);

  // A requirement the Go supervisor settles names the Go tests, and they exist.
  const goSource = readdirSync(join(REPO, "whatsapp-bridge"))
    .filter((name) => name.endsWith("_test.go"))
    .map((name) => readFileSync(join(REPO, "whatsapp-bridge", name), "utf8"))
    .join("\n");
  const goNamed = manifest.requirements.flatMap((requirement) => requirement.go ?? []);
  expect(manifest.requirements.filter((requirement) => requirement.go !== undefined).map((requirement) => requirement.id)).toEqual(["UI-01", "UI-02", "UI-03", "UI-07"]);
  for (const name of goNamed) expect(goSource, name).toMatch(new RegExp(`^func ${name}\\(t \\*testing\\.T\\)`, "m"));

  // Where the spec itself is present (it is git-ignored, so not in CI) the manifest must be exactly its IDs.
  const specPath = resolve(REPO, ".specs", "features", "interface-grafica", "spec.md");
  if (existsSync(specPath)) {
    const inSpec = [...readFileSync(specPath, "utf8").matchAll(/<!--\s*([A-Z]+-\d+)\s*-->/g)].map((match) => match[1] ?? "");
    expect([...ids].sort()).toEqual([...new Set(inSpec)].sort());
  }

  // No test is skipped, marked to fail or focused: a title is a promise that it runs.
  for (const name of readdirSync(E2E).filter((entry) => entry.endsWith(".spec.ts") && entry !== "parity-coverage.spec.ts")) {
    expect(readFileSync(join(E2E, name), "utf8"), name).not.toMatch(/\btest\.(skip|fixme|fail|only)\b|\.skip\(|\.only\(/);
  }
});

test("the coverage check reports an ID whose test title is gone", () => {
  const titles = ["AUTH-01 a title", "AUTH-02"];
  expect(idsWithoutATest(["AUTH-01", "AUTH-02", "AUTH-03"], titles)).toEqual(["AUTH-03"]);
  expect(idsWithoutATest(["AUTH-0"], titles)).toEqual(["AUTH-0"]);
  expect(idsWithoutATest(["AUTH-01"], ["AUTH-010 another"])).toEqual(["AUTH-01"]);
});
