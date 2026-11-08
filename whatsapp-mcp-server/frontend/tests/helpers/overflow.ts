/** jsdom has no layout: make every box report content taller than itself, as a long table would in a browser. */
export function mockOverflow(scrollHeight = 1000, clientHeight = 300): { restore: () => void } {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => clientHeight });
  return {
    restore: () => {
      Reflect.deleteProperty(HTMLElement.prototype, "scrollHeight");
      Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
    },
  };
}
