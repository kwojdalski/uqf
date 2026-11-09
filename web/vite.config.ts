import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Only API paths are proxied; the browser never sees gateway credentials.
//
// Every top-level path the API serves. `control` was missing when the
// setters landed (#199): the API answered /control, the built app served
// from the API reached it, and `npm run dev` returned Vite's own 404 for it -
// so the Control view reported "Request failed (404)" and nothing could be
// started. src/proxy.test.ts holds this list to the paths src/ actually
// requests.
export const API_PATHS = [
  "health",
  "catalog",
  "coverage",
  "query",
  "ops",
  "control",
  "activity",
  "services",
  "me",
  "settings",
  "charts",
  "compare",
  "workspaces",
  "workspace",
];

// The proxy adds no credential. It once added the write token (#631) to
// every request it forwarded, so anything that could reach the dev port - a
// page on another origin, another local user, a tunnel - made authorised
// writes (#2182). The dev UI now sends the token itself, as the built app
// at /ui/ does: the operator types it in the Control view (api.ts
// writeToken). src/proxy.test.ts holds the proxy to forwarding what the
// browser sent and nothing more.
export const proxy = {
  [`^/(${API_PATHS.join("|")})(/|\\?|$)`]: {
    target: process.env.UQF_API_ORIGIN || "http://127.0.0.1:8000",
  },
};
export default defineConfig({
  base: "/ui/",
  // scripts/check-bundle.mjs reads it, to hold Plotly out of the main bundle.
  build: { manifest: true },
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  test: { environment: "jsdom", setupFiles: "./src/test-setup.ts" },
});
