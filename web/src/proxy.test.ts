import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";
import { API_PATHS } from "../vite.config";

/**
 * Does the dev proxy forward every path the app requests?
 *
 * The proxy in vite.config.ts is an allowlist, and an allowlist drifts: the
 * setters (#199) added `/control` to the API and to the app, and the built
 * app - served from the API's own origin - reached it, while `npm run dev`
 * answered 404 from Vite itself. The Control view then reported a failed
 * request and nothing could be started. So the list is checked against the
 * paths the source actually names, not remembered.
 */
const src = join(import.meta.dirname);

const requested = new Set<string>();
for (const file of readdirSync(src)) {
  if (!/\.tsx?$/.test(file) || /\.test\.tsx?$/.test(file)) continue;
  const text = readFileSync(join(src, file), "utf8");
  // "/health", `/coverage?...`, `/control/process/${action}`: the first
  // segment after the leading slash, in a string or template literal.
  for (const m of text.matchAll(/["`]\/([a-z][a-z_-]*)/g)) requested.add(m[1]);
}
requested.delete("ui"); // the app's own base path, served by Vite

it("proxies every API path the app requests", () => {
  const missing = [...requested].filter((p) => !API_PATHS.includes(p)).sort();
  expect(missing).toEqual([]);
});

it("proxies nothing the app does not request", () => {
  const unused = API_PATHS.filter((p) => !requested.has(p)).sort();
  expect(unused).toEqual([]);
});

it("matches a path exactly, with a sub-path or a query, and nothing else", () => {
  const re = new RegExp(`^/(${API_PATHS.join("|")})(/|\\?|$)`);
  expect(re.test("/control")).toBe(true);
  expect(re.test("/control/process/start")).toBe(true);
  expect(re.test("/coverage?dataset=x")).toBe(true);
  expect(re.test("/controller")).toBe(false);
  expect(re.test("/ui/control")).toBe(false);
});
