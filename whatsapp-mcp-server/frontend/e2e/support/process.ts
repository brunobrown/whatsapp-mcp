import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cleanEnvironment, pythonCommand, SERVER_DIR } from "./harness";

/** An interface process started the way the bridge starts it (`python -m ui`), with only the environment it is given. */
export interface UiRun {
  child: ChildProcess;
  /** Everything it printed, stdout and stderr, line by line. */
  output: string[];
  /** Resolves with the exit code, or the signal's name when a signal ended it. */
  exited: Promise<string>;
  configDir: string;
  text(): string;
}

/**
 * Starts the interface with a configuration directory of its own. `env` is everything the process sees apart from the
 * system's: no WHATSAPP_* variable of the developer reaches it. `files` are written to its configuration directory first.
 * Call `end` when the test is done.
 */
export function runUi(env: Record<string, string>, files: Record<string, string> = {}): UiRun {
  const configDir = mkdtempSync(join(tmpdir(), "wmcp-e2e-launch-"));
  for (const [name, text] of Object.entries(files)) writeFileSync(join(configDir, name), text, { mode: 0o600 });
  const { command, args } = pythonCommand();
  const child = spawn(command, [...args, "-m", "ui"], {
    cwd: SERVER_DIR,
    env: { ...cleanEnvironment(), PYTHONUNBUFFERED: "1", WHATSAPP_CONFIG_DIR: configDir, ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const output: string[] = [];
  let partial = "";
  const collect = (chunk: Buffer): void => {
    partial += chunk.toString("utf8");
    const lines = partial.split("\n");
    partial = lines.pop() ?? "";
    output.push(...lines);
  };
  child.stdout.on("data", collect);
  child.stderr.on("data", collect);
  const exited = new Promise<string>((done) => {
    child.once("exit", (code, signal) => {
      if (partial !== "") output.push(partial);
      done(code === null ? String(signal) : String(code));
    });
  });
  return { child, output, exited, configDir, text: () => output.join("\n") };
}

/** Stops the process if it still runs and removes its directory. */
export async function end(run: UiRun): Promise<void> {
  if (run.child.exitCode === null && run.child.signalCode === null) {
    run.child.kill("SIGKILL");
    await run.exited;
  }
  rmSync(run.configDir, { recursive: true, force: true });
}

/** Waits for the line the bridge copies to its log, and returns the address in it. */
export async function waitForUrl(run: UiRun): Promise<string> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    for (const line of run.output) {
      const found = /interface at (http:\/\/\S+)/.exec(line);
      if (found?.[1] !== undefined) return found[1];
    }
    if (run.child.exitCode !== null) throw new Error(`The interface exited early: ${run.text()}`);
    await new Promise((done) => setTimeout(done, 50));
  }
  throw new Error(`The interface never printed its address: ${run.text()}`);
}

/** Waits until the address answers. */
export async function waitUntilServing(url: string, run: UiRun): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(`${url}/api/session`)).ok) return;
    } catch {
      // not listening yet
    }
    if (run.child.exitCode !== null) throw new Error(`The interface exited early: ${run.text()}`);
    await new Promise((done) => setTimeout(done, 100));
  }
  throw new Error("The interface did not serve in time.");
}

/** True when something accepts a connection at the address. */
export async function listening(url: string): Promise<boolean> {
  try {
    await fetch(`${url}/api/session`, { signal: AbortSignal.timeout(2000) });
    return true;
  } catch {
    return false;
  }
}
