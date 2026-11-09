import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import type { Services } from "./api/services";

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const SOURCE = "/repo/deploy_targets.toml";
function services(over: Partial<Services> = {}): Services {
  return {
    targets: [],
    source: SOURCE,
    declared: true,
    invalid: null,
    writes_enabled: false,
    poll_seconds: 30,
    ...over,
  };
}
/** Every request but /services answers as a quiet stack; /services answers
 *  with whatever `answer` returns next. */
function mockApi(answer: () => unknown) {
  const fetcher = vi.fn(async (path: string) =>
    path === "/services"
      ? answer()
      : path === "/health"
        ? ok({ ok: true, gateway: "up", poll_seconds: 5 })
        : ok({ writes_enabled: false, poll_seconds: 5 }),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function openServices() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Services/ }));
}
afterEach(() => vi.unstubAllGlobals());

it("lists each target's ssh destination, root and environment, profile or not", async () => {
  mockApi(() =>
    ok(
      services({
        targets: [
          {
            target: "prod-a",
            ssh: "deploy@prod-a",
            dest: "/srv/uqf",
            remote_user: null,
            environment: null,
            labels: { region: "ldn" },
            inspection: false,
            last_deployed: { release: "r42", at: "2026-11-01T09:00:00Z" },
          },
          {
            // ssh and root only, no deployment profile
            target: "uat-b",
            ssh: "deploy@uat-b",
            dest: "/srv/uqf-uat",
            remote_user: "svc",
            environment: "uat",
            labels: {},
            inspection: false,
            last_deployed: null,
          },
        ],
      }),
    ),
  );
  openServices();
  expect(await screen.findByText("deploy@prod-a")).toBeInTheDocument();
  expect(screen.getByText("/srv/uqf")).toBeInTheDocument();
  expect(screen.getByText("region=ldn")).toBeInTheDocument();
  expect(screen.getByText("r42 (2026-11-01T09:00:00Z)")).toBeInTheDocument();
  expect(screen.getByText("uat-b")).toBeInTheDocument();
  expect(screen.getByText("deploy@uat-b")).toBeInTheDocument();
  expect(screen.getByText("/srv/uqf-uat")).toBeInTheDocument();
  expect(screen.getByText("uat")).toBeInTheDocument();
  expect(screen.getByText("Source: " + SOURCE)).toBeInTheDocument();
});

it("says whether remote operations are enabled", async () => {
  mockApi(() => ok(services({ writes_enabled: true })));
  openServices();
  expect(
    await screen.findByText("Remote operations enabled"),
  ).toBeInTheDocument();
});

it("says remote operations are disabled when writes are off", async () => {
  mockApi(() => ok(services()));
  openServices();
  expect(
    await screen.findByText("Remote operations disabled"),
  ).toBeInTheDocument();
});

it("shows a loading state until the registry answers", async () => {
  let release: (value: unknown) => void = () => {};
  mockApi(() => new Promise((resolve) => (release = resolve)));
  openServices();
  expect(
    await screen.findByText("Loading deployment targets…"),
  ).toBeInTheDocument();
  release(ok(services()));
  await waitFor(() =>
    expect(
      screen.queryByText("Loading deployment targets…"),
    ).not.toBeInTheDocument(),
  );
});

it("shows an empty state when no declaration exists", async () => {
  mockApi(() => ok(services({ declared: false })));
  openServices();
  expect(
    await screen.findByText(/No deployment targets declared/),
  ).toBeInTheDocument();
});

it("shows an empty state when the declaration has no targets", async () => {
  mockApi(() => ok(services()));
  openServices();
  expect(
    await screen.findByText("The file declares no targets."),
  ).toBeInTheDocument();
});

it("shows an invalid configuration with the registry's reason", async () => {
  const reason = `${SOURCE}, line 3: target x: unknown setting(s) bogus`;
  mockApi(() => ok(services({ invalid: reason })));
  openServices();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Invalid deployment configuration");
  expect(alert).toHaveTextContent(reason);
  expect(screen.queryByText(/declares no targets/)).not.toBeInTheDocument();
});

it("refreshes on demand, reading the registry again", async () => {
  const answers = [
    services(),
    services({
      targets: [
        {
          target: "added",
          ssh: "ops@box",
          dest: "/opt/uqf",
          remote_user: null,
          environment: null,
          labels: {},
          inspection: false,
          last_deployed: null,
        },
      ],
    }),
  ];
  const fetcher = mockApi(() => ok(answers.shift() ?? answers[0]));
  openServices();
  expect(
    await screen.findByText("The file declares no targets."),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("ops@box")).toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter(([path]) => path === "/services"),
  ).toHaveLength(2);
});

it("marks a target in inspection mode, and only then shows the column", async () => {
  mockApi(() =>
    ok(
      services({
        targets: [
          {
            target: "prod-a",
            ssh: "deploy@prod-a",
            dest: "/srv/uqf",
            remote_user: null,
            environment: null,
            labels: {},
            inspection: true,
            last_deployed: null,
          },
        ],
      }),
    ),
  );
  openServices();
  expect(await screen.findByText("inspection mode")).toBeInTheDocument();
});
