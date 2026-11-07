import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./msw/server";

// jsdom lacks a few browser APIs that Radix primitives call; the stubs do nothing the tests rely on.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const stubs: Record<string, unknown> = {
  hasPointerCapture: () => false,
  setPointerCapture: () => undefined,
  releasePointerCapture: () => undefined,
  scrollIntoView: () => undefined,
};
if (typeof Element !== "undefined") {
  for (const [name, value] of Object.entries(stubs)) {
    if (!(name in Element.prototype)) Object.defineProperty(Element.prototype, name, { value, configurable: true });
  }
  if (!("ResizeObserver" in globalThis)) Object.defineProperty(globalThis, "ResizeObserver", { value: ResizeObserverStub });
}

beforeAll(() => {
  server.listen({ onUnhandledFrame: "error" });
});
afterEach(() => {
  server.resetHandlers();
});
afterAll(() => {
  server.close();
});
