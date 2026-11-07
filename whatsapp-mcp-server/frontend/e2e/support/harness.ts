import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, copyFileSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request } from "@playwright/test";
import type { Page } from "@playwright/test";
import { FakeBridge } from "./fakeBridge";

const FRONTEND = resolve(import.meta.dirname, "..", "..");
export const SERVER_DIR = resolve(FRONTEND, "..");
const HOOK_DIR = resolve(import.meta.dirname, "pyhook");
export const FIXTURE_DIR = join(SERVER_DIR, "tests", "fixtures", "ui");

/** The password every harness creates. Made up for the tests. */
export const PASSWORD = "correct horse battery staple";

export interface HarnessOptions {
  /** Create the first password before the test starts. Default true; false leaves the interface in first-run mode. */
  password?: boolean;
  /** Extra environment of the interface process. */
  env?: Record<string, string>;
  /** A folder `ui.sections` also searches for modules; created empty, so a test can drop a module into it. */
  extraSections?: boolean;
  /** The port of the interface; a free one by default. */
  port?: number;
  /** Files of tests/fixtures/ui (written by 0.6.0) copied into the configuration directory before the start. */
  seed?: string[];
  /** The idle limit of a session in seconds (the product's own is 30 minutes). */
  idleSeconds?: number;
}

export async function freePort(): Promise<number> {
  return new Promise((done, fail) => {
    const probe = createServer();
    probe.once("error", fail);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address() as AddressInfo;
      probe.close(() => {
        done(port);
      });
    });
  });
}

/** The newest modification time of the files under `dir`. */
function newest(dir: string): number {
  let latest = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    latest = Math.max(latest, entry.isDirectory() ? newest(path) : statSync(path).mtimeMs);
  }
  return latest;
}

/** The pages served are the built ones; a build older than the sources would test yesterday's interface. */
function assertPagesAreCurrent(): void {
  const index = join(SERVER_DIR, "ui", "web", "index.html");
  if (!existsSync(index)) {
    throw new Error("The interface pages are not built. Run `npx vite build` in whatsapp-mcp-server/frontend first.");
  }
  if (statSync(index).mtimeMs < newest(join(FRONTEND, "src"))) {
    throw new Error("The built pages are older than the sources. Run `npx vite build` in whatsapp-mcp-server/frontend.");
  }
}

/** The command that runs the interface's Python: the project's venv when there is one, else uv. */
function pythonCommand(): { command: string; args: string[] } {
  const override = process.env["WHATSAPP_E2E_PYTHON"];
  if (override) return { command: override, args: [] };
  const venv = join(SERVER_DIR, ".venv", "bin", "python");
  if (existsSync(venv)) return { command: venv, args: [] };
  return { command: "uv", args: ["run", "python"] };
}

/** The environment of the interface process: nothing from the developer's own WHATSAPP_* leaks in. */
function cleanEnvironment(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (!name.startsWith("WHATSAPP_") && !name.startsWith("E2E_")) env[name] = value;
  }
  return env;
}

/**
 * One interface process with its own configuration directory, port and fake bridge. Start it in `beforeAll`,
 * stop it in `afterAll`: `harnessForFile` does both. Nothing outlives `stop`.
 */
export class Harness {
  readonly configDir: string;
  readonly extraDir: string | undefined;
  readonly bridge: FakeBridge;
  port = 0;
  /** Everything the interface process printed, line by line. */
  readonly output: string[] = [];
  exitCode: number | null | undefined;
  private child: ChildProcess | undefined;
  private readonly options: HarnessOptions;
  private partial = "";

  constructor(options: HarnessOptions = {}) {
    this.options = options;
    this.configDir = mkdtempSync(join(tmpdir(), "wmcp-e2e-"));
    this.extraDir = options.extraSections ? mkdtempSync(join(tmpdir(), "wmcp-e2e-sections-")) : undefined;
    const token = randomBytes(32).toString("hex");
    writeFileSync(join(this.configDir, "api_token"), token, { mode: 0o600 });
    this.bridge = new FakeBridge(token);
    for (const name of options.seed ?? []) {
      copyFileSync(join(FIXTURE_DIR, name), this.file(name));
      chmodSync(this.file(name), 0o600);
    }
  }

  get url(): string {
    return `http://127.0.0.1:${String(this.port)}`;
  }

  /** A path inside the configuration directory. */
  file(name: string): string {
    return join(this.configDir, name);
  }

  /** The text of a file of the configuration directory, or undefined when it does not exist. */
  read(name: string): string | undefined {
    const path = this.file(name);
    return existsSync(path) ? readFileSync(path, "utf8") : undefined;
  }

  write(name: string, text: string): void {
    writeFileSync(this.file(name), text);
  }

  async start(): Promise<void> {
    assertPagesAreCurrent();
    await this.bridge.start();
    await this.launch();
    if (this.options.password !== false && !(this.options.seed ?? []).includes("ui_auth.json")) await this.createPassword();
  }

  /** Starts the interface process on the harness' port; the first call picks the port. */
  async launch(extraEnv: Record<string, string> = {}): Promise<void> {
    if (this.port === 0) this.port = this.options.port ?? (await freePort());
    const { command, args } = pythonCommand();
    const env: NodeJS.ProcessEnv = {
      ...cleanEnvironment(),
      WHATSAPP_CONFIG_DIR: this.configDir,
      WHATSAPP_UI_HOST: "127.0.0.1",
      WHATSAPP_UI_PORT: String(this.port),
      WHATSAPP_SEND_POLICY: this.file("send_policy.json"),
      WHATSAPP_SEND_AUDIT_LOG: this.file("send_audit.log"),
      WHATSAPP_MONITOR_CONFIG: this.file("monitor_profiles.json"),
      WHATSAPP_MONITOR_AUDIT_LOG: this.file("monitor_audit.log"),
      WHATSAPP_MONITOR_DRAFTS: this.file("monitor_drafts.json"),
      PYTHONPATH: [HOOK_DIR, SERVER_DIR].join(":"),
      PYTHONUNBUFFERED: "1",
      E2E_BRIDGE_URL: this.bridge.url,
      ...(this.extraDir !== undefined && { E2E_EXTRA_SECTIONS_DIR: this.extraDir }),
      ...(this.options.idleSeconds !== undefined && { E2E_IDLE_SECONDS: String(this.options.idleSeconds) }),
      ...this.options.env,
      ...extraEnv,
    };
    this.exitCode = undefined;
    const child = spawn(command, [...args, "-m", "ui"], { cwd: SERVER_DIR, env, stdio: ["ignore", "pipe", "pipe"] });
    this.child = child;
    const collect = (chunk: Buffer): void => {
      this.partial += chunk.toString("utf8");
      const lines = this.partial.split("\n");
      this.partial = lines.pop() ?? "";
      this.output.push(...lines);
    };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);
    child.once("exit", (code) => {
      this.exitCode = code ?? -1;
    });
    await this.untilServing();
  }

  private async untilServing(): Promise<void> {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (this.exitCode !== undefined) {
        throw new Error(`The interface exited with ${String(this.exitCode)} before it served: ${this.output.join(" | ")}`);
      }
      try {
        const response = await fetch(`${this.url}/api/session`);
        if (response.ok) return;
      } catch {
        // not listening yet
      }
      await new Promise((done) => setTimeout(done, 100));
    }
    throw new Error(`The interface did not start in time: ${this.output.join(" | ")}`);
  }

  /** The newest setup code the process printed after the line `after`, waiting for it if it has not come yet. */
  async setupCode(after = 0): Promise<string> {
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
      for (const line of this.output.slice(after)) {
        const found = /setup code for the interface: (\S+)/.exec(line);
        if (found?.[1] !== undefined) return found[1];
      }
      await new Promise((done) => setTimeout(done, 50));
    }
    throw new Error("The interface printed no setup code.");
  }

  /** Creates the first password through the API, with the code the process printed. */
  private async createPassword(): Promise<void> {
    const code = await this.setupCode();
    const api = await request.newContext({ baseURL: this.url });
    try {
      const session = (await (await api.get("/api/session")).json()) as { csrf: string };
      const answer = await api.post("/api/session/setup", {
        headers: { "X-CSRF-Token": session.csrf },
        data: { code, password: PASSWORD, confirm: PASSWORD },
      });
      expect(((await answer.json()) as { ok: boolean }).ok).toBe(true);
    } finally {
      await api.dispose();
    }
  }

  /** Signs in through the page, the way an operator does, and waits for the shell. */
  async signIn(page: Page, password = PASSWORD): Promise<void> {
    await page.goto(`${this.url}/`);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("shell")).toBeVisible();
  }

  /** Asks the process to end and waits for it; a process that ignores the request is killed after a few seconds. */
  async halt(): Promise<number> {
    const child = this.child;
    if (child === undefined || this.exitCode !== undefined) return this.lastExit();
    const ended = new Promise<void>((done) => child.once("exit", () => {
      done();
    }));
    child.kill("SIGTERM");
    const killer = setTimeout(() => child.kill("SIGKILL"), 8000);
    await ended;
    clearTimeout(killer);
    return this.lastExit();
  }

  private lastExit(): number {
    return this.exitCode ?? -1;
  }

  /** Stops the process, then starts it again over the same files. */
  async restart(extraEnv: Record<string, string> = {}): Promise<void> {
    await this.halt();
    await this.launch(extraEnv);
  }

  /** Stops the process and the fake bridge and removes every directory the harness made. */
  async stop(): Promise<void> {
    await this.halt();
    await this.bridge.stop();
    rmSync(this.configDir, { recursive: true, force: true });
    if (this.extraDir !== undefined) rmSync(this.extraDir, { recursive: true, force: true });
  }
}
