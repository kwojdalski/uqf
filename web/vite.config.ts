import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Only API paths are proxied; the browser never sees gateway credentials.
const proxy = {
  "^/(health|catalog|coverage|query|ops)(/|\\?|$)": {
    target: process.env.UQF_API_ORIGIN || "http://127.0.0.1:8000",
  },
};
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  test: { environment: "jsdom", setupFiles: "./src/test-setup.ts" },
});
