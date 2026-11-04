import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import type { Services, TunnelCheck, TunnelMapping, TunnelPlan } from "./api";
import { connectionText, FleetConnections, mappingMarks } from "./TunnelPanel";

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const SERVICES: Services = {
  targets: [
    {
      name: "prod",
      ssh: "ops@prod.example",
      dest: "/srv/uqf",
      remote_user: null,
      environment: null,
      labels: {},
      last_deployed: null,
    },
  ],
  source: "/repo/deploy_targets.toml",
  declared: true,
  invalid: null,
  remote_operations_enabled: true,
  poll_seconds: 30,
};
function mapping(over: Partial<TunnelMapping> = {}): TunnelMapping {
  return {
    process: "rdb1",
    role: "rdb",
    remote_host: "prod.example",
    remote_port: 6002,
    local_host: "127.0.0.1",
    local_port: 16002,
    production_endpoint: "prod.example:6002",
    local_endpoint: "127.0.0.1:16002",
    query_command: "uqs query --tunnel prod rdb1",
    state: "verified",
    last_check: {
      at: "2026-11-04T12:00:00+00:00",
      identity: "rdb1",
      remote_port: 6002,
      local_port: 16002,
      release: "r9",
    },
    listening: true,
    ...over,
  };
}
const PLAN: TunnelPlan = {
  target: "prod",
  ssh: "ops@prod.example",
  release: null,
  mappings: [
    mapping(),
    mapping({
      process: "hdb1",
      role: "hdb",
      remote_port: 6003,
      local_port: 16003,
      production_endpoint: "prod.example:6003",
      local_endpoint: "127.0.0.1:16003",
      query_command: "uqs query --tunnel prod hdb1",
      state: "stale",
      listening: false,
    }),
  ],
  launch_argv: ["ssh", "-N", "ops@prod.example"],
  launch_command:
    "ssh -o BatchMode=yes -N -L 127.0.0.1:16002:127.0.0.1:6002 ops@prod.example",
  available: ["gateway1"],
};
const CHECK: TunnelCheck = {
  ok: false,
  target: "prod",
  ssh: "ops@prod.example",
  release: "r9",
  checked_at: "2026-11-04T12:30:00+00:00",
  category: null,
  error: null,
  launch_command: PLAN.launch_command,
  endpoints: [
    {
      process: "rdb1",
      role: "rdb",
      production_endpoint: "prod.example:6002",
      local_endpoint: "127.0.0.1:16002",
      remote_port: 6002,
      local_port: 16002,
      status: "ok",
      detail: "",
      layers: { ssh: "ok", destination: "ok" },
      connected: true,
      identity: "rdb1",
      gateway_servers: null,
      last_success: null,
    },
    {
      process: "hdb1",
      role: "hdb",
      production_endpoint: "prod.example:6003",
      local_endpoint: "127.0.0.1:16003",
      remote_port: 6003,
      local_port: 16003,
      status: "auth_refused",
      detail: "hdb1 refused the q login: Wrong credential.",
      layers: { ssh: "ok", destination: "ok", auth: "failed" },
      connected: false,
      identity: null,
      gateway_servers: null,
      last_success: null,
    },
  ],
  categories: { auth_refused: "the process refused the q login" },
};

let writeText: ReturnType<typeof vi.fn>;
beforeEach(() => {
  writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});
afterEach(() => vi.unstubAllGlobals());

function mockApi() {
  const fetcher = vi.fn(async (path: string) =>
    path === "/services"
      ? ok(SERVICES)
      : path.startsWith("/services/prod/tunnel/check")
        ? ok(CHECK)
        : path.startsWith("/services/prod/tunnel")
          ? ok(PLAN)
          : path === "/health"
            ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
            : ok({ writes_enabled: false, poll_seconds: 5 }),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
async function openTunnel() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Services/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Tunnel prod" }));
  await screen.findByText("Tunnel to prod");
}

it("shows uqs's mappings: production and local endpoints together, marked", async () => {
  mockApi();
  await openTunnel();
  expect(await screen.findByText("prod.example:6002")).toBeInTheDocument();
  expect(screen.getByText("127.0.0.1:16002")).toBeInTheDocument();
  expect(screen.getByText("stale")).toBeInTheDocument();
  expect(screen.getByText("disconnected")).toBeInTheDocument();
  expect(screen.getByText(PLAN.launch_command)).toBeInTheDocument();
  // a declared process the plan did not select can be added
  expect(screen.getByLabelText("gateway1")).not.toBeChecked();
});

it("copies the local endpoint, the details and the launch command as uqs gave them", async () => {
  mockApi();
  await openTunnel();
  fireEvent.click(
    await screen.findByRole("button", { name: "Copy rdb1 endpoint" }),
  );
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith("127.0.0.1:16002"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Copy hdb1 details" }));
  await waitFor(() =>
    expect(writeText).toHaveBeenLastCalledWith(
      expect.stringContaining("hdb1 (hdb) [stale, disconnected]"),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Copy launch command" }));
  await waitFor(() =>
    expect(writeText).toHaveBeenLastCalledWith(PLAN.launch_command),
  );
});

it("asks uqs for another selection rather than editing the plan itself", async () => {
  const fetcher = mockApi();
  await openTunnel();
  fireEvent.click(await screen.findByLabelText("gateway1"));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      "/services/prod/tunnel?process=gateway1&process=hdb1&process=rdb1",
      expect.anything(),
    ),
  );
});

it("shows each endpoint's diagnostic, healthy beside failed", async () => {
  mockApi();
  await openTunnel();
  fireEvent.click(
    await screen.findByRole("button", { name: "Check connections" }),
  );
  expect(
    await screen.findByText("hdb1 refused the q login: Wrong credential."),
  ).toBeInTheDocument();
  expect(screen.getByText("auth_refused")).toBeInTheDocument();
  expect(screen.getByText("connected")).toBeInTheDocument();
});

it("marks a mapping by uqs's state and whether its port answers now", () => {
  expect(
    mappingMarks(mapping({ state: "unverified", listening: false })),
  ).toEqual(["unverified", "disconnected"]);
  const text = connectionText(mapping());
  expect(text).toContain("production: prod.example:6002");
  expect(text).toContain("local: 127.0.0.1:16002");
  expect(text).toContain("query: uqs query --tunnel prod rdb1");
  expect(text).not.toMatch(/pass|admin/i);
});

it("offers the Fleet view the same connection details for a chosen target", async () => {
  mockApi();
  render(<FleetConnections />);
  const select = await screen.findByLabelText(/Deployment target/);
  fireEvent.change(select, { target: { value: "prod" } });
  fireEvent.click(
    await screen.findByRole("button", { name: "Copy rdb1 details" }),
  );
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining("rdb1 (rdb) [verified, listening]"),
    ),
  );
});
