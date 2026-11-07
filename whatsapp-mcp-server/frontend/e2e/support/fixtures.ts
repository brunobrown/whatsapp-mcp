import { test as base } from "@playwright/test";
import { Harness } from "./harness";
import type { HarnessOptions } from "./harness";

export { expect, request } from "@playwright/test";
export { FIXTURE_DIR, Harness, PASSWORD } from "./harness";

/**
 * Starts one interface per spec file in `beforeAll` and removes it in `afterAll`: a fresh configuration directory,
 * a free port and a fake bridge. Call it at the top of the file; read the harness inside the tests with `.current`.
 */
export function harnessForFile(options: HarnessOptions = {}): { readonly current: Harness } {
  const holder: { value: Harness | undefined } = { value: undefined };
  base.beforeAll(async () => {
    const harness = new Harness(options);
    holder.value = harness;
    await harness.start();
  });
  base.afterAll(async () => {
    await holder.value?.stop();
    holder.value = undefined;
  });
  return {
    get current(): Harness {
      if (holder.value === undefined) throw new Error("The harness is not running.");
      return holder.value;
    },
  };
}

export const test = base;

/** A harness of its own for one test that needs a different start (first run, a seeded file, a short idle limit). */
export async function withHarness<T>(options: HarnessOptions, body: (harness: Harness) => Promise<T>): Promise<T> {
  const harness = new Harness(options);
  try {
    await harness.start();
    return await body(harness);
  } finally {
    await harness.stop();
  }
}

/**
 * After a refused password the product makes the next try wait one second (AUTH-04). This lets that second pass
 * before the next try; it waits for a rule of the server, not for the page to render.
 */
export async function throttlePause(): Promise<void> {
  await new Promise((done) => setTimeout(done, 1200));
}
