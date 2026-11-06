// @vitest-environment node
import { createServer as createHttpServer } from "node:http";
import type { AddressInfo } from "node:net";
import { join } from "node:path";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

/**
 * Does the dev proxy add a credential the browser did not send?
 *
 * It did (#2182): vite.config.ts set `Authorization: Bearer
 * $UQF_FRONTEND_WRITE_TOKEN` on every request it forwarded, so anything that
 * could reach the dev port - a page on another origin, another local user, a
 * tunnel - made authorised writes. Now the dev UI sends the token itself, as
 * the built app does, and the proxy forwards what arrived and nothing more.
 * Checked against a real Vite dev server in front of a stand-in API, with the
 * token set in the environment the config is loaded from.
 */
const seen: Array<{ url?: string; authorization?: string }> = [];
const api = createHttpServer((req, res) => {
  seen.push({ url: req.url, authorization: req.headers.authorization });
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end("{}");
});
let vite: ViteDevServer;
let base = "";
const saved = {
  origin: process.env.UQF_API_ORIGIN,
  token: process.env.UQF_FRONTEND_WRITE_TOKEN,
};

beforeAll(async () => {
  await new Promise<void>((done) => api.listen(0, "127.0.0.1", done));
  const { port } = api.address() as AddressInfo;
  process.env.UQF_API_ORIGIN = `http://127.0.0.1:${port}`;
  process.env.UQF_FRONTEND_WRITE_TOKEN = "dev-secret";
  const web = join(import.meta.dirname, "..");
  vite = await createServer({
    root: web,
    configFile: join(web, "vite.config.ts"),
    logLevel: "silent",
    server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
  });
  await vite.listen();
  const address = vite.httpServer?.address() as AddressInfo;
  base = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  await vite?.close();
  await new Promise((done) => api.close(done));
  for (const [key, value] of [
    ["UQF_API_ORIGIN", saved.origin],
    ["UQF_FRONTEND_WRITE_TOKEN", saved.token],
  ] as const) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

async function forwarded(path: string, headers: Record<string, string>) {
  seen.length = 0;
  const response = await fetch(base + path, { method: "POST", headers });
  expect(response.status).toBe(200);
  expect(seen.map((s) => s.url)).toEqual([path]);
  return seen[0].authorization;
}

it("adds no Authorization to a request from a foreign origin", async () => {
  expect(
    await forwarded("/control/lifecycle/start", {
      Origin: "http://evil.example",
    }),
  ).toBeUndefined();
});

it("adds no Authorization to a request from the dev page's own origin", async () => {
  expect(
    await forwarded("/control/lifecycle/start", { Origin: base }),
  ).toBeUndefined();
});

it("forwards the token the dev UI sends itself, unchanged", async () => {
  expect(
    await forwarded("/control/lifecycle/start", {
      Origin: base,
      Authorization: "Bearer typed-by-the-operator",
    }),
  ).toBe("Bearer typed-by-the-operator");
});
