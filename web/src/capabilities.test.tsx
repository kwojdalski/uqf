import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import type { Capabilities } from "./api";
import { configureActivity, queuedActivity, record } from "./activity";

const ok = (body: unknown) => ({ ok: true, json: async () => body });

function capabilities(
  identity: string,
  groups: string[],
  granted: [string, "read" | "write"][],
): Capabilities {
  return {
    identity,
    authenticated: true,
    groups,
    capabilities: granted.map(([feature, action]) => ({ feature, action })),
    features: {},
    poll_seconds: 60,
  };
}

/** Two groups with different views, as the server's policy would grant. */
const VIEWER = capabilities(
  "bob",
  ["viewers"],
  [
    ["desk", "read"],
    ["coverage", "read"],
  ],
);
const OPERATOR = capabilities(
  "carol",
  ["operators"],
  [
    ["control", "read"],
    ["fleet", "read"],
  ],
);

function serve(me: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path === "/health")
        return ok({ ok: true, gateway: "up", poll_seconds: 5 });
      if (path === "/me/capabilities") return ok(me);
      if (path === "/control")
        return ok({
          writes_enabled: true,
          lifecycle_actions: ["start", "stop", "restart"],
          settable_fields: [],
          processes: [],
          poll_seconds: 5,
        });
      return ok({ poll_seconds: 60, rows: [] });
    }),
  );
}

function navNames(): string[] {
  const nav = screen.getByRole("navigation", { name: "Workspace views" });
  return Array.from(nav.querySelectorAll("button")).map(
    (b) => b.textContent?.replace(/^\d+/, "") ?? "",
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  configureActivity({ transport: async () => {} });
});

it("shows a viewer only the views their group may read", async () => {
  serve(VIEWER);
  render(<App />);
  await waitFor(() => expect(navNames()).toEqual(["Coverage", "Desk"]));
  expect(screen.getByText(/Signed in as bob/)).toBeInTheDocument();
});

it("shows an operator a different set, starting on one they may read", async () => {
  serve(OPERATOR);
  render(<App />);
  await waitFor(() => expect(navNames()).toEqual(["Fleet", "Control"]));
  // Coverage, the default view, is not theirs: the first view that is shows.
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Fleet");
});

it("says changes are not part of a caller's access without control:write", async () => {
  serve(OPERATOR);
  render(<App />);
  await waitFor(() => expect(navNames()).toContain("Control"));
  fireEvent.click(screen.getByRole("button", { name: /Control/ }));
  expect(
    await screen.findByText("Changes are not part of your access"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /restart/i }),
  ).not.toBeInTheDocument();
});

it("shows every view when the server's answer is not a policy", async () => {
  serve({ unexpected: true });
  render(<App />);
  await waitFor(() => expect(navNames()).toHaveLength(10));
  expect(screen.getByText(/Local session/)).toBeInTheDocument();
});

it("says so when the policy grants no view at all", async () => {
  serve(capabilities("eve", [], []));
  render(<App />);
  expect(
    await screen.findByText(/Your access includes none of these views/),
  ).toBeInTheDocument();
});

it("records no activity for a caller without activity:write", async () => {
  configureActivity({ transport: async () => {}, flushDelayMs: 60_000 });
  serve(VIEWER);
  render(<App />);
  await waitFor(() => expect(navNames()).toEqual(["Coverage", "Desk"]));
  expect(record("navigate", "view.open", { target: "Desk" })).toBeNull();
  expect(queuedActivity()).toEqual([]);
});
