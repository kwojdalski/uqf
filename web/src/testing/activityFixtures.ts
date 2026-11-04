/**
 * A whole API answering every view with something to show, for the activity
 * tests (#2000): the coverage test renders every view with its controls on
 * screen, and the level tests drive the same app at each level.
 */
import { vi } from "vitest";
import type { ActivityHistory, SessionLevel } from "../api";

export const ok = (body: unknown) => ({
  ok: true,
  status: 200,
  json: async () => body,
});

export const HISTORY: ActivityHistory = {
  events: [
    {
      event_id: "e1",
      ts: "2026-11-03T10:00:00.000000Z",
      recorded_at: "2026-11-03T10:00:00.000000Z",
      level: "actions",
      source: "server",
      kind: "request",
      action: "process.stop",
      target: "rdb1 rdb2",
      outcome: "unknown",
      session_id: null,
      request_id: "req0123456789",
      claimed_user: "alice",
      trigger: null,
      method: "POST",
      status_code: 422,
      duration_ms: 35000,
      items: [
        { target: "rdb1", outcome: "unknown" },
        { target: "rdb2", outcome: "unknown" },
      ],
      detail: {},
      dropped: 0,
    },
  ],
  persistence: {
    persistent: false,
    path: null,
    failures: 2,
    last_error: "OperationalError: disk I/O error",
    last_failure_at: null,
  },
  default_level: "actions",
  bounds: [
    { level: "actions", retention_days: 30, max_rows: 100000 },
    { level: "interactions", retention_days: 7, max_rows: 50000 },
    { level: "debug", retention_days: 1, max_rows: 10000 },
  ],
  drops: [
    {
      level: "debug",
      reason: "sampled",
      count: 12,
      last_at: "2026-11-03T10:00:00.000000Z",
    },
  ],
  poll_seconds: 15,
};

const PROCESSES = [
  { procname: "rdb1", proctype: "rdb", start_with_all: true },
  { procname: "rdb2", proctype: "rdb", start_with_all: true },
  { procname: "feed1", proctype: "feed", start_with_all: false },
];

function listing(path: string) {
  return {
    target: "prod-a",
    root: "/srv/uqf",
    path,
    parent: path ? "" : null,
    entries: [
      {
        name: "releases",
        kind: "dir",
        size: null,
        mtime: 0,
        blocked: false,
        reason: null,
      },
      {
        name: "notes.txt",
        kind: "file",
        size: 12,
        mtime: 0,
        blocked: false,
        reason: null,
      },
    ],
    total: 2,
    truncated: false,
    release: {
      current: "releases/r42",
      release: "r42",
      report: { revision: "abc1234" },
      report_error: null,
    },
  };
}

/** What every path the app reads answers with. */
export function answer(path: string, level: SessionLevel = "actions"): unknown {
  const bare = path.split("?")[0];
  if (bare === "/health")
    return {
      ok: true,
      gateway: "up",
      poll_seconds: 5,
      request_timeout_seconds: 45,
      detail: null,
      activity_level: level,
    };
  if (bare === "/control")
    return {
      writes_enabled: true,
      lifecycle_actions: ["start", "stop", "restart"],
      settable_fields: ["extras", "port"],
      processes: PROCESSES,
      poll_seconds: 5,
    };
  if (bare === "/ops/processes")
    return {
      processes: PROCESSES.map((p) => ({
        ...p,
        group: "g",
        host: "localhost",
        declared_port: 1,
        port: 1,
        up: p.procname !== "feed1",
      })),
      poll_seconds: 5,
    };
  if (bare === "/ops/backfill")
    return {
      summary: { running: 0 },
      workers: [],
      unreadable: [],
      source: "/status",
      poll_seconds: 5,
    };
  if (bare.startsWith("/ops/")) return { rows: [{ a: 1 }], poll_seconds: 5 };
  if (bare === "/charts")
    return {
      charts: [
        {
          name: "markout_curve",
          title: "Markout curve",
          description: "Markout by horizon",
          point_limit: 600,
          params: {
            properties: {
              dataset: { type: "string", enum: ["fx_markout"] },
              sym: { type: "string", default: "" },
              days: { type: "integer", default: 14, minimum: 1 },
              hdb: { type: "boolean", default: false },
            },
          },
        },
      ],
    };
  if (bare === "/catalog")
    return {
      tables: [
        {
          name: "trades",
          description: "Trades",
          columns: [
            { name: "sym", type: "symbol", filterable: true, decimals: null },
            { name: "px", type: "float", filterable: true, decimals: 2 },
          ],
        },
        { name: "quotes", description: "Quotes", columns: [] },
      ],
      operators: ["eq", "in"],
    };
  if (bare === "/query")
    return {
      table: "trades",
      tier: "rdb",
      rows: [{ sym: "EURUSD", px: 1.1 }],
      row_count: 1,
      truncated: false,
      poll_seconds: 5,
    };
  if (bare === "/coverage")
    return {
      dataset: "trades",
      source_version: "v1",
      covered: [],
      requested: null,
      gaps: [],
      complete: true,
      poll_seconds: 30,
    };
  if (bare === "/services")
    return {
      targets: [
        {
          name: "prod-a",
          ssh: "deploy@prod-a",
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
  if (bare.startsWith("/services/prod-a/files"))
    return listing(
      new URLSearchParams(path.split("?")[1] ?? "").get("path") ?? "",
    );
  if (bare === "/activity") return HISTORY;
  if (bare.startsWith("/control/process/"))
    return {
      action: "stop",
      target: "all",
      exit_code: 0,
      ok: true,
      output: "done",
    };
  return {};
}

/** Stub fetch with `answer`, at `level` as the server's default. */
export function mockWholeApi(level: SessionLevel = "actions") {
  const fetcher = vi.fn(async (path: string) => ok(answer(path, level)));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
