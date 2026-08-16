import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** Recharts measures its container with ResizeObserver, which jsdom does not implement. Without
 *  this the chart renders at zero width and the <Line> never mounts, so every chart assertion would
 *  pass or fail for a reason having nothing to do with the code under test. */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as never;
