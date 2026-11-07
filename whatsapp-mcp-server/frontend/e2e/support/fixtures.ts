import { test as base } from "@playwright/test";
import { Harness } from "./harness";
import type { HarnessOptions } from "./harness";

export { expect } from "@playwright/test";
export { Harness, PASSWORD } from "./harness";

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
