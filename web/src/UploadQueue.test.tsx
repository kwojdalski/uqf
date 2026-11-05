import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  type ConfigPushReport,
  type PushOps,
  type RemoteUpload,
  UPLOAD_MAX_FILES,
  UPLOAD_MAX_PATH,
  UploadFailed,
  uploadPathProblem,
} from "./api";
import { UploadPanel, type Uploader } from "./UploadPanel";

/** The upload queue's bookkeeping across renders: what happens when the
 *  panel goes away mid-run (#2007), when files arrive after an async read
 *  (#2012), when the queue empties (#2013), and the server's path limit
 *  (#2014). UploadPanel.test.tsx has the panel's ordinary behaviour. */

function file(name: string): File {
  return new File(["a,b\n"], name);
}

function answer(path: string): RemoteUpload {
  return {
    target: "prod-a",
    dest: "/srv/uqf",
    path,
    size: 4,
    sha256: "0".repeat(64),
    replaced: false,
    mode: 0o644,
    created_dirs: [],
    push_id: "20261104T103442123Z-9aa36609",
    previous_size: null,
    previous_sha256: null,
  };
}

/** Push calls that open one push and record how it was closed. */
function fakePushes() {
  const closed: ConfigPushReport[][] = [];
  const pushes: PushOps = {
    open: (target) =>
      Promise.resolve({
        target,
        dest: "/srv/uqf",
        push_id: "20261104T103442123Z-9aa36609",
        pruned: [],
        keep: 50,
        keep_days: 90,
      }),
    close: (_target, _push, reported) => {
      closed.push(reported);
      return Promise.resolve({} as never);
    },
  };
  return { pushes, closed };
}

function panel(
  uploader: Uploader,
  directory = "shared/config",
  pushes = fakePushes().pushes,
) {
  const props = {
    target: "prod-a",
    dest: "/srv/uqf",
    onUploaded: () => {},
    uploader,
    pushes,
  };
  const view = render(<UploadPanel {...props} directory={directory} />);
  return {
    ...view,
    open: (next: string) =>
      view.rerender(<UploadPanel {...props} directory={next} />),
  };
}

const idle: Uploader = (_t, path) => Promise.resolve(answer(path));

function choose(files: File[]) {
  fireEvent.change(screen.getByLabelText(/Choose files/), {
    target: { files },
  });
}

function confirm() {
  fireEvent.click(screen.getByRole("button", { name: /^Upload \d+ files?…$/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm upload" }));
}

it("aborts the upload in flight and sends nothing more when the panel goes away (#2007)", async () => {
  const calls: { path: string; signal: AbortSignal }[] = [];
  // as uploadRemoteFile does: an abort in flight is an uncertain failure
  const uploader: Uploader = (_t, path, _f, _o, _p, signal) => {
    calls.push({ path, signal });
    return new Promise((_ok, fail) =>
      signal.addEventListener("abort", () =>
        fail(new UploadFailed("The upload was cancelled in flight", 0, true)),
      ),
    );
  };
  const { pushes, closed } = fakePushes();
  const { unmount } = panel(uploader, "shared/config", pushes);
  choose([file("a.csv"), file("b.csv"), file("c.csv")]);
  confirm();
  await vi.waitFor(() => expect(calls).toHaveLength(1));
  unmount(); // Close, or Browse another target, or another view
  expect(calls[0].signal.aborted).toBe(true);
  await vi.waitFor(() => expect(closed).toHaveLength(1));
  expect(calls.map((call) => call.path)).toEqual(["shared/config/a.csv"]);
  // the push is closed with how each file ended: the one in flight may have
  // been written, the rest were never sent
  expect(closed[0]).toEqual([
    { path: "shared/config/a.csv", outcome: "unknown" },
    { path: "shared/config/b.csv", outcome: "cancelled" },
    { path: "shared/config/c.csv", outcome: "cancelled" },
  ]);
});

it("holds a dropped folder to the queue as it is when the folder is read (#2012)", async () => {
  panel(idle);
  let release!: () => void;
  const read = new Promise<void>((ok) => (release = ok));
  const slow = {
    isFile: true,
    isDirectory: false,
    fullPath: "/late.csv",
    file: (ok: (f: File) => void) => void read.then(() => ok(file("late.csv"))),
  } as unknown as FileSystemEntry;
  fireEvent.drop(screen.getByRole("region", { name: /Drop files/ }), {
    dataTransfer: {
      items: [{ kind: "file", webkitGetAsEntry: () => slow }],
      files: [],
    },
  });
  // the queue fills up while the drop is still being read
  choose(Array.from({ length: UPLOAD_MAX_FILES }, (_, i) => file(`f${i}`)));
  await act(async () => release());
  await vi.waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      `at most ${UPLOAD_MAX_FILES} files`,
    ),
  );
  expect(document.querySelectorAll('tr[data-status="queued"]')).toHaveLength(
    UPLOAD_MAX_FILES,
  );
});

it("refuses a dropped file already queued while the drop was read (#2012)", async () => {
  panel(idle);
  let release!: () => void;
  const read = new Promise<void>((ok) => (release = ok));
  const slow = {
    isFile: true,
    isDirectory: false,
    fullPath: "/same.csv",
    file: (ok: (f: File) => void) => void read.then(() => ok(file("same.csv"))),
  } as unknown as FileSystemEntry;
  fireEvent.drop(screen.getByRole("region", { name: /Drop files/ }), {
    dataTransfer: {
      items: [{ kind: "file", webkitGetAsEntry: () => slow }],
      files: [],
    },
  });
  choose([file("same.csv")]);
  await act(async () => release());
  await vi.waitFor(() =>
    expect(document.querySelectorAll("tbody tr")).toHaveLength(2),
  );
  expect(document.querySelectorAll('tr[data-status="queued"]')).toHaveLength(1);
  expect(screen.getByText("already queued")).toBeInTheDocument();
});

it("follows the open directory again once the queue is emptied (#2013)", () => {
  const { open } = panel(idle, "shared/config/a");
  choose([file("x.csv")]);
  fireEvent.click(screen.getByRole("button", { name: "Remove x.csv" }));
  open("shared/config/b");
  choose([file("y.csv")]);
  expect(screen.getByText("/srv/uqf/shared/config/b/")).toBeInTheDocument();
  expect(screen.queryByText(/where these files were queued/)).toBeNull();
});

it("queues to the open directory after a finished run, without Clear finished (#2013)", async () => {
  const calls: string[] = [];
  const { open } = panel((t, path, ...rest) => {
    calls.push(path);
    return idle(t, path, ...rest);
  }, "shared/config/a");
  choose([file("x.csv")]);
  confirm();
  await vi.waitFor(() => expect(screen.getByText("Uploaded")).toBeTruthy());
  open("shared/config/b");
  choose([file("y.csv")]);
  expect(screen.queryByText(/where these files were queued/)).toBeNull();
  confirm();
  await vi.waitFor(() => expect(calls).toHaveLength(2));
  expect(calls).toEqual(["shared/config/a/x.csv", "shared/config/b/y.csv"]);
});

it("keeps the destination while files are still queued there (#2013)", () => {
  const { open } = panel(idle, "shared/config/a");
  choose([file("x.csv")]);
  open("shared/config/b");
  choose([file("y.csv")]);
  expect(screen.getByText("/srv/uqf/shared/config/a/")).toBeInTheDocument();
  expect(screen.getByText(/where these files were queued/)).toBeInTheDocument();
});

it("refuses a path longer than the server's limit (#2014)", () => {
  expect(UPLOAD_MAX_PATH).toBe(4096);
  // 16 directories of 255 characters: within the depth and name limits
  const dirs = Array.from({ length: 16 }, (_, i) => `${i}`.padEnd(255, "d"));
  const long = ["shared", "config", ...dirs, "f.csv"].join("/");
  expect(long.length).toBeGreaterThan(UPLOAD_MAX_PATH);
  expect(uploadPathProblem(long)).toBe(
    "the path is longer than 4096 characters",
  );
  // exactly at the limit is allowed
  const full = ["shared", "config", ...dirs.slice(0, 15), "x".repeat(236)];
  const fits = [...full, "f.csv"].join("/");
  expect(fits.length).toBe(UPLOAD_MAX_PATH);
  expect(uploadPathProblem(fits)).toBeNull();
});
