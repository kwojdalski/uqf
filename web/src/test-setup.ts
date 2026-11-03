import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { configureActivity } from "./activity";
// No API in a test: activity is collected and dropped unless a test sends
// it somewhere it can read (activity.test.ts).
beforeEach(() => configureActivity({ transport: async () => {} }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
