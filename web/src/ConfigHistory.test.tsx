import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  ApiError,
  type ConfigDiff,
  type ConfigHistory as History,
  type ConfigPushDetail,
  type ConfigPushFile,
  type ConfigRestore,
  type HistoryOps,
} from "./api";
import { ConfigHistory, shortHash } from "./ConfigHistory";

const PUSH = "20261104T103442123Z-9aa36609";
const OLD = "a".repeat(64);
const NEW = "b".repeat(64);

function file(over: Partial<ConfigPushFile>): ConfigPushFile {
  return {
    path: "shared/config/app.env",
    action: "replace",
    size: 4,
    sha256: NEW,
    mode: 0o640,
    previous_size: 3,
    previous_sha256: OLD,
    previous_mode: 0o640,
    created_dirs: [],
    state: "published",
    error: null,
    at: "2026-11-04T10:34:42Z",
    now: "after",
    now_sha256: NEW,
    now_error: null,
    ...over,
  };
}

const HISTORY: History = {
  target: "prod-a",
  dest: "/srv/uqf",
  pushes: [
    {
      push_id: PUSH,
      kind: "push",
      restores: null,
      created: "2026-11-04T10:34:42Z",
      closed: "2026-11-04T10:34:43Z",
      user: "kw",
      target: "prod-a",
      via: "frontend",
      config_dir: "shared/config",
      declared: { file_count: 3, total_bytes: 9 },
      file_count: 3,
      total_bytes: 9,
      states: { published: 2, pending: 0, failed: 1 },
      reported_count: 0,
      error: null,
    },
  ],
  total: 1,
  truncated: false,
  pruned: [
    {
      push_id: "20261001T000000000Z-00000001",
      why: "row_cap",
      pruned_at: "2026-11-04T10:34:42Z",
    },
  ],
};

const DETAIL: ConfigPushDetail = {
  target: "prod-a",
  dest: "/srv/uqf",
  summary: HISTORY.pushes[0],
  push: {
    push_id: PUSH,
    kind: "push",
    restores: null,
    created: "2026-11-04T10:34:42Z",
    closed: "2026-11-04T10:34:43Z",
    user: "kw",
    target: "prod-a",
    via: "frontend",
    config_dir: "shared/config",
    declared: { file_count: 3, total_bytes: 9 },
    reported: [{ path: "shared/config/lost", outcome: "unknown" }],
    files: [
      file({}),
      file({
        path: "shared/config/new.bin",
        action: "create",
        previous_sha256: null,
        previous_size: null,
      }),
      file({ path: "shared/config/refused", state: "failed", error: "exists" }),
    ],
  },
};

const DIFFS: Record<string, ConfigDiff["files"][number]> = {
  "shared/config/app.env": {
    path: "shared/config/app.env",
    action: "replace",
    previous_size: 3,
    size: 4,
    previous_sha256: OLD,
    sha256: NEW,
    kind: "text",
    diff: "--- a/shared/config/app.env\n+++ b/shared/config/app.env\n@@ -1 +1 @@\n-a=1\n+a=22\n",
  },
  "shared/config/new.bin": {
    path: "shared/config/new.bin",
    action: "create",
    previous_size: null,
    size: 2048,
    previous_sha256: null,
    sha256: NEW,
    kind: "binary",
    diff: null,
  },
};

function plan(steps: ConfigRestore["plan"]): ConfigRestore {
  return {
    target: "prod-a",
    dest: "/srv/uqf",
    restores: PUSH,
    dry_run: true,
    push_id: null,
    plan: steps,
    results: [],
    pruned: [],
  };
}

const CLEAN = plan([
  {
    path: "shared/config/app.env",
    step: "write",
    now_sha256: NEW,
    restore_sha256: OLD,
    error: null,
  },
  {
    path: "shared/config/new.bin",
    step: "delete",
    now_sha256: NEW,
    restore_sha256: null,
    error: null,
  },
]);

function fakeOps(over: Partial<HistoryOps> = {}) {
  const calls: string[] = [];
  const ops: HistoryOps = {
    list: async () => {
      calls.push("list");
      return HISTORY;
    },
    show: async (_t, push) => {
      calls.push(`show ${push}`);
      return DETAIL;
    },
    diff: async (_t, push, path) => {
      calls.push(`diff ${push} ${path}`);
      return {
        target: "prod-a",
        dest: "/srv/uqf",
        push_id: push,
        diff_cap: 262144,
        files: [DIFFS[path as string]],
      };
    },
    plan: async (_t, push) => {
      calls.push(`plan ${push}`);
      return CLEAN;
    },
    restore: async (_t, push) => {
      calls.push(`restore ${push}`);
      return {
        ...CLEAN,
        dry_run: false,
        push_id: "20261104T103500000Z-00000002",
        results: [
          {
            path: "shared/config/app.env",
            step: "write",
            outcome: "succeeded",
            error: null,
          },
          {
            path: "shared/config/new.bin",
            step: "delete",
            outcome: "succeeded",
            error: null,
          },
        ],
      };
    },
    ...over,
  };
  return { ops, calls };
}

function view(ops: HistoryOps) {
  const onRestored = vi.fn();
  render(<ConfigHistory target="prod-a" ops={ops} onRestored={onRestored} />);
  return { onRestored };
}

async function showPush() {
  fireEvent.click(screen.getByRole("button", { name: "Show push history" }));
  fireEvent.click(
    await screen.findByRole("button", { name: `Show push ${PUSH}` }),
  );
  return screen.findByRole("region", { name: `Push ${PUSH}` });
}

it("reads nothing from the server until asked, then lists the pushes and the pruned", async () => {
  const { ops, calls } = fakeOps();
  view(ops);
  expect(calls).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Show push history" }));
  const table = await screen.findByLabelText("Pushes");
  expect(within(table).getByText(PUSH)).toBeInTheDocument();
  expect(table).toHaveTextContent("push to shared/config");
  expect(table).toHaveTextContent("1 failed");
  expect(screen.getByLabelText("Pruned pushes")).toHaveTextContent(
    "20261001T000000000Z-00000001 (row_cap",
  );
});

it("shows each file's change, old and new hashes, and what it is now", async () => {
  const { ops } = fakeOps();
  view(ops);
  const push = await showPush();
  const rows = within(push).getAllByRole("row");
  expect(rows[1]).toHaveTextContent("shared/config/app.env");
  expect(rows[1]).toHaveTextContent("replaced");
  expect(rows[1]).toHaveTextContent(shortHash(OLD));
  expect(rows[1]).toHaveTextContent(shortHash(NEW));
  expect(rows[1]).toHaveTextContent("as the push left it");
  expect(rows[2]).toHaveTextContent("created");
  expect(rows[2]).toHaveTextContent("-"); // no previous hash on create
  expect(rows[3]).toHaveTextContent("failed (exists)");
  expect(within(rows[3]).queryByRole("button", { name: /Diff/ })).toBeNull();
  expect(push).toHaveTextContent("shared/config/lost (unknown)");
});

it("fetches a diff on demand: text as a diff, binary as sizes and hashes", async () => {
  const { ops, calls } = fakeOps();
  view(ops);
  await showPush();
  expect(calls.some((c) => c.startsWith("diff"))).toBe(false);
  fireEvent.click(
    screen.getByRole("button", { name: "Diff shared/config/app.env" }),
  );
  const text = await screen.findByLabelText("Diff of shared/config/app.env");
  expect(text.tagName).toBe("PRE");
  expect(text).toHaveTextContent("-a=1");
  expect(text).toHaveTextContent("+a=22");
  fireEvent.click(
    screen.getByRole("button", { name: "Diff shared/config/new.bin" }),
  );
  const binary = await screen.findByLabelText("Diff of shared/config/new.bin");
  expect(binary.tagName).toBe("P");
  expect(binary).toHaveTextContent("binary - not diffed");
  expect(binary).toHaveTextContent(shortHash(NEW));
  expect(calls).toContain(`diff ${PUSH} shared/config/new.bin`);
});

it("restores only after the plan is confirmed, once, then reloads", async () => {
  const { ops, calls } = fakeOps();
  const { onRestored } = view(ops);
  await showPush();
  fireEvent.click(screen.getByRole("button", { name: "Restore…" }));
  const confirm = await screen.findByRole("group", { name: "Confirm restore" });
  expect(within(confirm).getByLabelText("Restore plan")).toHaveTextContent(
    "delete it (the push created it)",
  );
  expect(calls.filter((c) => c.startsWith("restore"))).toEqual([]);
  fireEvent.click(
    within(confirm).getByRole("button", { name: "Confirm restore of 2 files" }),
  );
  const results = await screen.findByLabelText("Restore results");
  expect(results).toHaveTextContent("20261104T103500000Z-00000002");
  expect(results).toHaveTextContent("deleted");
  expect(calls.filter((c) => c.startsWith("restore"))).toEqual([
    `restore ${PUSH}`,
  ]);
  expect(onRestored).toHaveBeenCalledTimes(1);
  expect(calls.filter((c) => c === "list")).toHaveLength(2);
});

it("offers no restore when a file changed since the push", async () => {
  const { ops } = fakeOps({
    plan: async () =>
      plan([
        { ...CLEAN.plan[0] },
        { ...CLEAN.plan[1], step: "conflict", now_sha256: OLD },
      ]),
  });
  view(ops);
  await showPush();
  fireEvent.click(screen.getByRole("button", { name: "Restore…" }));
  const confirm = await screen.findByRole("group", { name: "Confirm restore" });
  expect(within(confirm).getByRole("alert")).toHaveTextContent(
    "1 file(s) changed since the push",
  );
  expect(
    within(confirm).queryByRole("button", { name: /Confirm restore/ }),
  ).toBeNull();
});

it("tells a refused restore from an uncertain one, and retries neither", async () => {
  for (const [failure, said] of [
    [
      new ApiError("changed since", false, 409),
      "Restore refused - nothing was written",
    ],
    [new ApiError("no answer", false, 504), "Restore outcome unknown"],
    [new ApiError("API unreachable", true), "Restore outcome unknown"],
  ] as const) {
    let attempts = 0;
    const { ops } = fakeOps({
      restore: async () => {
        attempts += 1;
        throw failure;
      },
    });
    const { unmount } = render(
      <ConfigHistory target="prod-a" ops={ops} onRestored={() => {}} />,
    );
    await showPush();
    fireEvent.click(screen.getByRole("button", { name: "Restore…" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm restore of 2 files" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(said);
    expect(attempts).toBe(1);
    unmount();
  }
});

it("says plainly when a push's history was pruned", async () => {
  const { ops } = fakeOps({
    plan: async () => {
      throw new ApiError(
        `push ${PUSH}'s history was pruned at 2026-11-05T00:00:00Z (row_cap bound) - it can no longer be shown, diffed or restored`,
        false,
        410,
      );
    },
  });
  view(ops);
  await showPush();
  fireEvent.click(screen.getByRole("button", { name: "Restore…" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "can no longer be shown, diffed or restored",
  );
  expect(screen.queryByRole("group", { name: "Confirm restore" })).toBeNull();
});

it("reports a restore that succeeded as restored when reloading the history after it fails (#2011)", async () => {
  let lists = 0;
  const { ops } = fakeOps({
    list: async () => {
      lists += 1;
      if (lists > 1) throw new ApiError("Enter the write token", false, 401);
      return HISTORY;
    },
  });
  const { onRestored } = view(ops);
  await showPush();
  fireEvent.click(screen.getByRole("button", { name: "Restore…" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Confirm restore of 2 files" }),
  );
  expect(await screen.findByLabelText("Restore results")).toHaveTextContent(
    "20261104T103500000Z-00000002",
  );
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(
    "Restored; reloading the history failed: Enter the write token",
  );
  expect(alert).not.toHaveTextContent("Restore refused");
  expect(alert).not.toHaveTextContent("Restore outcome unknown");
  expect(onRestored).toHaveBeenCalledTimes(1);
});
