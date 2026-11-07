import { vi } from "vitest";

type Listener = (event: { matches: boolean }) => void;

/**
 * Controls `window.matchMedia` for `(min-width: Npx)` queries against a width the test sets,
 * since jsdom does not lay anything out. Call `restore` in afterEach.
 */
export function mockViewport(initialWidth: number): { setWidth: (width: number) => void; restore: () => void } {
  let width = initialWidth;
  const listeners = new Set<{ query: string; fn: Listener }>();
  const matches = (query: string): boolean => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return min?.[1] !== undefined ? width >= Number(min[1]) : false;
  };
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      return matches(query);
    },
    media: query,
    addEventListener: (_: string, fn: Listener) => listeners.add({ query, fn }),
    removeEventListener: (_: string, fn: Listener) => {
      for (const item of listeners) if (item.fn === fn) listeners.delete(item);
    },
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
    onchange: null,
  }));
  return {
    setWidth: (next) => {
      width = next;
      for (const item of [...listeners]) item.fn({ matches: matches(item.query) });
    },
    restore: () => {
      vi.unstubAllGlobals();
      listeners.clear();
    },
  };
}
