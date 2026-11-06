import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import type { Capabilities } from "./api";
import { configureActivity, INTERACTIVE } from "./activity";
import { ok, SETTINGS } from "./testing/activityFixtures";

const NAME = /^[A-Za-z0-9_.:/@ ,+-]{1,200}$/;

function me(granted: [string, "read" | "write"][]): Capabilities {
  return {
    user: "alice",
    authenticated: true,
    groups: [],
    capabilities: granted.map(([feature, action]) => ({ feature, action })),
    features: {},
    inspection: {
      active: false,
      targets: [],
      q_login: "service",
      registry_error: null,
    },
    poll_seconds: 60,
  };
}
const ADMIN = me([
  ["settings", "read"],
  ["settings", "write"],
]);
const READER = me([["settings", "read"]]);

function serve(
  caps: Capabilities,
  put: (path: string, body: unknown) => unknown = () =>
    ok({
      changes: [{ field: "activity_level", before: "actions", after: "debug" }],
      version: "c".repeat(64),
    }),
) {
  const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "PUT")
      return put(path, JSON.parse(String(init.body))) as Response;
    if (path === "/health")
      return ok({ ok: true, gateway: "up", poll_seconds: 5 });
    if (path === "/me/capabilities") return ok(caps);
    if (path === "/settings") return ok(SETTINGS);
    return ok({ poll_seconds: 60 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

afterEach(() => {
  vi.unstubAllGlobals();
  configureActivity({ transport: async () => {} });
});

async function openSettings() {
  render(<App />);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: /Settings/ })).toHaveLength(1),
  );
  fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
  await screen.findByRole("heading", { name: "Access policy" });
}

it("renders both forms from the server's schemas, and the secrets' presence only", async () => {
  serve(ADMIN);
  await openSettings();
  expect(
    screen.getByRole("heading", { name: "Runtime settings" }),
  ).toBeInTheDocument();
  // the schema's titles, not a list of this file's own
  expect(screen.getAllByText("Who").length).toBeGreaterThan(0);
  expect(screen.getByText("Default activity level")).toBeInTheDocument();
  expect(
    screen.getByRole("cell", { name: "UQF_FRONTEND_WRITE_TOKEN" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Set")).toBeInTheDocument();
  expect(screen.getByText("Not set")).toBeInTheDocument();
});

it("saves a change with the version it was edited from, and shows the diff", async () => {
  const fetcher = serve(ADMIN);
  await openSettings();
  fireEvent.change(screen.getByLabelText(/Default activity level/), {
    // RJSF's select offers an enum by its index: "debug" is the fourth
    target: { value: "3" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save runtime settings" }),
  );
  expect(await screen.findByText("activity_level")).toBeInTheDocument();
  const [path, init] = fetcher.mock.calls.find(
    ([, init]) => init?.method === "PUT",
  )!;
  expect(path).toBe("/settings/runtime");
  const body = JSON.parse(String(init!.body));
  expect(body.version).toBe(SETTINGS.runtime_version);
  expect(body.settings.activity_level).toBe("debug");
});

it("shows the server's refusal - a lockout, here", async () => {
  serve(ADMIN, () => ({
    ok: false,
    status: 409,
    json: async () => ({
      error: "SettingsConflict",
      detail: "refused: no caller could change the policy after this",
      transient: false,
    }),
  }));
  await openSettings();
  fireEvent.click(screen.getByRole("button", { name: "Save access policy" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "no caller could change the policy",
  );
});

it("disables the forms for a caller without settings:write, and says why", async () => {
  serve(READER);
  await openSettings();
  expect(
    screen.queryByRole("button", { name: /^Save/ }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByText(/does not include settings:write/).length).toBe(2);
  expect(screen.getByLabelText(/Default activity level/)).toBeDisabled();
});

it("names every control the form library renders", async () => {
  serve(ADMIN);
  await openSettings();
  const unnamed = [...document.querySelectorAll(INTERACTIVE)]
    .filter((el) => !NAME.test(el.getAttribute("data-activity") ?? ""))
    .map((el) => el.outerHTML.slice(0, 120));
  expect(unnamed).toEqual([]);
  expect(
    document.querySelector('[data-activity="settings.runtime.activity_level"]'),
  ).not.toBeNull();
  expect(
    document.querySelector('[data-activity="settings.access.grants.subject"]'),
  ).not.toBeNull();
});
