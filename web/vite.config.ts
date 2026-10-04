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
];

// The write token (#631), added here on the server side so a dev browser
// never holds it. The built app served at /ui/ has no proxy in front of it;
// there the Control view asks for the token instead (api.ts writeToken).
const writeToken = process.env.UQF_FRONTEND_WRITE_TOKEN;
const proxy = {
  [`^/(${API_PATHS.join("|")})(/|\\?|$)`]: {
    target: process.env.UQF_API_ORIGIN || "http://127.0.0.1:8000",
    headers: writeToken ? { Authorization: `Bearer ${writeToken}` } : undefined,
  },
};
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  test: { environment: "jsdom", setupFiles: "./src/test-setup.ts" },
});
