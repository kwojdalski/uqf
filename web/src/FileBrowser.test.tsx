import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";
import type { RemoteEntry, RemoteFiles, Services } from "./api";
import { remoteFilesPath } from "./api";
import { size } from "./FileTable";

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const failed = (status: number, detail: string) => ({
  ok: false,
  status,
  json: async () => ({ error: "x", detail, transient: false }),
});

function services(remote: boolean): Services {
  return {
    targets: [
      {
        target: "prod-a",
        ssh: "deploy@prod-a",
        dest: "/srv/uqf",
        remote_user: null,
        environment: null,
        labels: {},
        inspection: false,
        last_deployed: null,
      },
    ],
    source: "/repo/deploy_targets.toml",
    declared: true,
    invalid: null,
    writes_enabled: remote,
    poll_seconds: 30,
  };
}

function entry(over: Partial<RemoteEntry>): RemoteEntry {
  return {
    name: "x",
    kind: "file",
    size: null,
    mtime: "1970-01-01T00:00:00.000000000Z",
    blocked: false,
    reason: null,
    ...over,
  };
}

function listing(path: string, entries: RemoteEntry[]): RemoteFiles {
  return {
    target: "prod-a",
    dest: "/srv/uqf",
    path,
    parent: path ? path.split("/").slice(0, -1).join("/") : null,
    entries,
    total: entries.length,
    truncated: false,
    release: {
      current: "releases/r42",
      release: "r42",
      report: { revision: "abc1234", status: "deployed", profile: "essential" },
      report_error: null,
    },
  };
}

const ODD = 'it\'s a "dir" -x $(id)';
const LISTINGS: Record<string, RemoteFiles> = {
  "": listing("", [
    entry({ name: "releases", kind: "dir" }),
    entry({ name: "shared", kind: "dir" }),
    entry({
      name: "current",
      kind: "symlink",
      blocked: true,
      reason: "symbolic link - not followed",
    }),
  ]),
  shared: listing("shared", [
    entry({ name: ODD, kind: "dir" }),
    entry({ name: "sources.csv", kind: "file", size: 12345 }),
    entry({
      name: "pipe",
      kind: "special",
      blocked: true,
      reason: "FIFO - not supported",
    }),
  ]),
  [`shared/${ODD}`]: listing(`shared/${ODD}`, []),
};

/** /services answers with `remote`; each listing from LISTINGS by path. */
function mockApi(remote = true, files?: (path: string) => unknown) {
  const fetcher = vi.fn(async (url: string) => {
    if (url === "/services") return ok(services(remote));
    if (url.startsWith("/services/")) {
      const path = new URL(url, "http://x").searchParams.get("path") ?? "";
      return files ? files(path) : ok(LISTINGS[path]);
    }
    if (url === "/health")
      return ok({ ok: true, gateway: "up", poll_seconds: 5 });
    return ok({ writes_enabled: false, poll_seconds: 5 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function filesRequests(fetcher: ReturnType<typeof mockApi>): string[] {
  return fetcher.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.startsWith("/services/"));
}
async function openBrowser() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Services/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Browse prod-a" }));
  return screen.findByRole("region", { name: "Files on prod-a" });
}
afterEach(() => vi.unstubAllGlobals());

it("lists the root with types, release metadata and a blocked link", async () => {
  mockApi();
  const panel = await openBrowser();
  expect(await within(panel).findByText("/srv/uqf")).toBeInTheDocument();
  const release = within(panel).getByLabelText("Release");
  expect(within(release).getByText("r42")).toBeInTheDocument();
  expect(within(release).getByText("abc1234")).toBeInTheDocument();
  expect(within(release).getByText("deployed")).toBeInTheDocument();
  expect(
    within(panel).getByRole("button", { name: "releases/" }),
  ).toBeInTheDocument();
  // a link is shown, blocked, and cannot be opened
  expect(
    within(panel).getByText("Blocked: symbolic link - not followed"),
  ).toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: /current/ })).toBeNull();
  expect(within(panel).getByRole("button", { name: "Up" })).toBeDisabled();
});

it("navigates into a directory, shows sizes, and goes back up", async () => {
  const fetcher = mockApi();
  const panel = await openBrowser();
  fireEvent.click(
    await within(panel).findByRole("button", { name: "shared/" }),
  );
  expect(await within(panel).findByText("12.1 KiB")).toBeInTheDocument();
  expect(
    within(panel).getByText("Blocked: FIFO - not supported"),
  ).toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "pipe/" })).toBeNull();
  // an unusual name is navigated as itself, encoded only in the query string
  fireEvent.click(within(panel).getByRole("button", { name: `${ODD}/` }));
  expect(
    await within(panel).findByText("This directory is empty."),
  ).toBeInTheDocument();
  expect(filesRequests(fetcher)).toContain(
    remoteFilesPath("prod-a", `shared/${ODD}`),
  );
  fireEvent.click(within(panel).getByRole("button", { name: "Up" }));
  expect(await within(panel).findByText("sources.csv")).toBeInTheDocument();
  fireEvent.click(within(panel).getByRole("button", { name: "Up" }));
  expect(
    await within(panel).findByRole("button", { name: "releases/" }),
  ).toBeInTheDocument();
});

it("refreshes only when asked: no polling over ssh", async () => {
  const fetcher = mockApi();
  const panel = await openBrowser();
  await within(panel).findByRole("button", { name: "releases/" });
  expect(filesRequests(fetcher)).toHaveLength(1);
  fireEvent.click(within(panel).getByRole("button", { name: "Refresh" }));
  await vi.waitFor(() => expect(filesRequests(fetcher)).toHaveLength(2));
  expect(filesRequests(fetcher)[1]).toBe(remoteFilesPath("prod-a", ""));
});

it("shows the server's refusal, such as a release without its python", async () => {
  mockApi(true, () =>
    failed(
      409,
      "target prod-a: /srv/uqf/current/.venv/bin/python is missing or not executable",
    ),
  );
  const panel = await openBrowser();
  const alert = await within(panel).findByRole("alert");
  expect(alert).toHaveTextContent("Could not list this directory");
  expect(alert).toHaveTextContent(".venv/bin/python is missing");
});

it("points at the write token when the API asks for it", async () => {
  mockApi(true, () => failed(401, "control actions need the write token"));
  const panel = await openBrowser();
  expect(await within(panel).findByRole("alert")).toHaveTextContent(
    "Enter the write token in the Control view",
  );
});

it("offers no browsing while remote operations are disabled", async () => {
  const fetcher = mockApi(false);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: /Services/ }));
  expect(
    await screen.findByText(/needs remote operations enabled/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Browse/ })).toBeNull();
  expect(filesRequests(fetcher)).toEqual([]);
});

it("encodes the target and path in the URL, never in the path segment", () => {
  expect(remoteFilesPath("a/b", "x/../y?z")).toBe(
    "/services/a%2Fb/files?path=x%2F..%2Fy%3Fz",
  );
});

it("formats sizes in binary units", () => {
  expect(size(null)).toBe("");
  expect(size(0)).toBe("0 B");
  expect(size(1023)).toBe("1023 B");
  expect(size(12345)).toBe("12.1 KiB");
  expect(size(5 * 1024 ** 3)).toBe("5.0 GiB");
});
