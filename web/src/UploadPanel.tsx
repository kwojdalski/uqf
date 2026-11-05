import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  type ConfigPushReport,
  type PushOps,
  pushOps,
  type RemoteUpload,
  UPLOAD_MAX_BYTES,
  UPLOAD_MAX_FILES,
  UPLOAD_MAX_TOTAL_BYTES,
  UPLOAD_ROOT,
  UploadFailed,
  uploadPathProblem,
  uploadRemoteFile,
} from "./api";
import { size } from "./FileTable";

/** A file the operator picked, with its path relative to the destination:
 * its name, or - from a folder or a drop - the folder path above it. */
export interface Picked {
  file: File;
  relative: string;
}

type Status =
  | "queued"
  | "invalid"
  | "uploading"
  | "done"
  | "failed"
  | "unknown"
  | "cancelled";

interface Item extends Picked {
  id: number;
  status: Status;
  progress: number;
  message: string;
}

export type Uploader = typeof uploadRemoteFile;

const STATUS: Record<Status, string> = {
  queued: "Queued",
  invalid: "Not uploadable",
  uploading: "Uploading",
  done: "Uploaded",
  failed: "Failed - nothing written",
  unknown: "Outcome unknown",
  cancelled: "Cancelled - not sent",
};

/** Is `directory` (relative to the deployment root) one uploads may go to? */
export function uploadable(directory: string): boolean {
  return directory === UPLOAD_ROOT || directory.startsWith(`${UPLOAD_ROOT}/`);
}

/** Every file under a dropped file or folder, with its path in the drop.
 * The entries are taken from the DataTransfer by the caller, synchronously:
 * it is emptied once the drop event returns. */
export async function walkEntries(
  entries: FileSystemEntry[],
): Promise<Picked[]> {
  const out: Picked[] = [];
  const visit = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      out.push({ file, relative: entry.fullPath.replace(/^\/+/, "") });
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // readEntries answers in batches until it answers none
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        if (batch.length === 0) break;
        for (const child of batch) await visit(child);
      }
    }
  };
  for (const entry of entries) await visit(entry);
  return out;
}

/** What the browser reports, on closing the push, of a file it saw end so. */
const REPORTED: Partial<Record<Status, ConfigPushReport["outcome"]>> = {
  unknown: "unknown",
  failed: "failed",
  cancelled: "cancelled",
};

/** Upload local files and folders below a target's shared/config (#1951).
 *
 *  Files are queued, checked against the same limits the server holds, and
 *  sent only after the operator confirms the destination - one at a time,
 *  each with its own outcome. Replacing an existing file is off unless
 *  asked for. A cancellation or timeout in flight is reported as unknown
 *  - the file may have been written - and nothing is ever retried.
 *
 *  Confirming opens one push (#2002): every file names its id, the server
 *  keeps what each replaced in that push's history, and the push is closed
 *  - with what the browser saw of each file - once the last is sent. */
export function UploadPanel({
  target,
  dest,
  directory,
  onUploaded,
  uploader = uploadRemoteFile,
  pushes = pushOps,
}: {
  target: string;
  dest: string;
  directory: string;
  onUploaded: () => void;
  uploader?: Uploader;
  /** Injectable for tests; the real ones POST to /config/pushes. */
  pushes?: PushOps;
}) {
  const [items, setShown] = useState<Item[]>([]);
  // The queue and its destination as they are now, not as the last render
  // saw them: a dropped folder is added when its read finishes, which can be
  // after other files were queued (#2012).
  const current = useRef<{ items: Item[]; destination: string | null }>({
    items: [],
    destination: null,
  });
  const [destination, setPinned] = useState<string | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [running, setRunning] = useState(false);
  const [position, setPosition] = useState({ at: 0, of: 0 });
  const [refusal, setRefusal] = useState<string | null>(null);
  const [push, setPush] = useState<{ id: string; pruned: number } | null>(null);
  const [closeProblem, setCloseProblem] = useState<string | null>(null);
  const nextId = useRef(1);
  const controller = useRef<AbortController | null>(null);
  const unmounted = useRef(false);
  useEffect(() => {
    unmounted.current = false;
    return () => {
      // Close, another target or another view: nothing is sent unseen. The
      // run sees the abort, reports the rest cancelled and closes its push
      // (#2007).
      unmounted.current = true;
      controller.current?.abort();
    };
  }, []);

  const setItems = (change: (items: Item[]) => Item[]) => {
    current.current.items = change(current.current.items);
    setShown(current.current.items);
  };
  const setDestination = (value: string | null) => {
    current.current.destination = value;
    setPinned(value);
  };

  const queued = items.filter((item) => item.status === "queued");
  // The destination is held only while something is queued or sending; an
  // emptied queue follows the open directory again (#2013).
  const pinned = running || queued.length > 0 ? destination : null;
  const configDir = pinned ?? directory;
  const queuedBytes = queued.reduce((sum, item) => sum + item.file.size, 0);

  const update = (id: number, change: Partial<Item>) =>
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...change } : item)),
    );

  function add(picked: Picked[]) {
    setRefusal(null);
    if (picked.length === 0) return;
    const pending = current.current.items.filter(
      (item) => item.status === "queued",
    );
    const base =
      (pending.length > 0 ? current.current.destination : null) ?? directory;
    if (!uploadable(base)) {
      setRefusal(`Open ${UPLOAD_ROOT}/ or a directory in it to upload.`);
      return;
    }
    if (pending.length + picked.length > UPLOAD_MAX_FILES) {
      setRefusal(
        `A queue holds at most ${UPLOAD_MAX_FILES} files; this would make ${pending.length + picked.length}.`,
      );
      return;
    }
    const total =
      pending.reduce((sum, item) => sum + item.file.size, 0) +
      picked.reduce((sum, item) => sum + item.file.size, 0);
    if (total > UPLOAD_MAX_TOTAL_BYTES) {
      setRefusal(
        `A queue holds at most ${size(UPLOAD_MAX_TOTAL_BYTES)}; this would make ${size(total)}.`,
      );
      return;
    }
    const seen = new Set(pending.map((item) => item.relative));
    const added = picked.map(({ file, relative }): Item => {
      const problem =
        file.size > UPLOAD_MAX_BYTES
          ? `larger than the ${size(UPLOAD_MAX_BYTES)} limit`
          : seen.has(relative)
            ? "already queued"
            : uploadPathProblem(`${base}/${relative}`);
      seen.add(relative);
      return {
        file,
        relative,
        id: nextId.current++,
        status: problem ? "invalid" : "queued",
        progress: 0,
        message: problem ?? "",
      };
    });
    setDestination(base);
    setConfirming(false);
    setItems((current) => [...current, ...added]);
  }

  function fromInput(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    add(
      files.map((file) => ({
        file,
        relative: file.webkitRelativePath || file.name,
      })),
    );
    event.target.value = "";
  }

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (running) return;
    const transfer = event.dataTransfer;
    const entries = Array.from(transfer.items ?? [])
      .map((item) =>
        item.kind === "file" ? (item.webkitGetAsEntry?.() ?? null) : null,
      )
      .filter((entry): entry is FileSystemEntry => entry !== null);
    if (entries.length === 0) {
      add(
        Array.from(transfer.files ?? []).map((file) => ({
          file,
          relative: file.name,
        })),
      );
      return;
    }
    walkEntries(entries).then(add, (error: unknown) =>
      setRefusal(`Could not read what was dropped: ${String(error)}`),
    );
  }

  async function start() {
    setConfirming(false);
    setRunning(true);
    setRefusal(null);
    setCloseProblem(null);
    // Armed before the push is opened, so a Cancel pressed during the open
    // round trip is honoured (#2010). The open itself is not aborted: it
    // runs to its answer, so the push it made is closed, not left open.
    const abort = new AbortController();
    controller.current = abort;
    let opened: string;
    try {
      const answer = await pushes.open(target, {
        config_dir: configDir,
        file_count: queued.length,
        total_bytes: queuedBytes,
      });
      opened = answer.push_id;
      setPush({ id: opened, pruned: answer.pruned.length });
    } catch (error: unknown) {
      controller.current = null;
      if (unmounted.current) return;
      setRunning(false);
      setRefusal(
        `The push could not be opened, so nothing was sent: ${error instanceof Error ? error.message : String(error)}`,
      );
      return;
    }
    let wrote = false;
    const reported: ConfigPushReport[] = [];
    for (const [index, item] of queued.entries()) {
      setPosition({ at: index + 1, of: queued.length });
      const path = `${configDir}/${item.relative}`;
      if (abort.signal.aborted) {
        update(item.id, { status: "cancelled", message: "" });
        reported.push({ path, outcome: "cancelled" });
        continue;
      }
      update(item.id, { status: "uploading", progress: 0 });
      try {
        const done: RemoteUpload = await uploader(
          target,
          path,
          item.file,
          overwrite,
          (progress) => update(item.id, { progress }),
          abort.signal,
          opened,
        );
        wrote = true;
        update(item.id, {
          status: "done",
          progress: 1,
          message: done.replaced ? "replaced an existing file" : "",
        });
      } catch (error: unknown) {
        const unsure = error instanceof UploadFailed ? error.uncertain : true;
        if (unsure) wrote = true;
        const status: Status = unsure ? "unknown" : "failed";
        reported.push({ path, outcome: REPORTED[status] ?? "failed" });
        update(item.id, {
          status,
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
    controller.current = null;
    try {
      await pushes.close(target, opened, reported);
    } catch (error: unknown) {
      if (unmounted.current) return;
      setCloseProblem(
        `Push ${opened} was not closed: ${error instanceof ApiError ? error.message : String(error)}. Its files are in its history; the summary of the push is not.`,
      );
    }
    if (unmounted.current) return;
    setRunning(false);
    if (wrote) onUploaded();
  }

  function clearFinished() {
    setItems((all) => all.filter((item) => item.status === "queued"));
  }

  const finished = items.some(
    (item) => item.status !== "queued" && item.status !== "uploading",
  );
  const canUpload = uploadable(configDir);
  return (
    <section className="panel" aria-label="Upload files">
      <h3>Upload to {target}</h3>
      {!canUpload ? (
        <p className="note">
          Uploads go below <code>{UPLOAD_ROOT}/</code> - the operator files
          every release links in. Open it, or a directory in it, to upload.
        </p>
      ) : (
        <>
          <p className="note">
            Destination:{" "}
            <code>
              {dest}/{configDir}/
            </code>
            {pinned && pinned !== directory && (
              <> (where these files were queued, not the open directory)</>
            )}
          </p>
          <div className="actions">
            <label>
              Choose files{" "}
              <input
                type="file"
                data-activity="files.upload.files"
                multiple
                disabled={running}
                onChange={fromInput}
              />
            </label>
            <label>
              Choose a folder{" "}
              <input
                type="file"
                data-activity="files.upload.folder"
                multiple
                disabled={running}
                ref={(element) => element?.setAttribute("webkitdirectory", "")}
                onChange={fromInput}
              />
            </label>
          </div>
          <div
            className="drop-zone"
            role="region"
            aria-label="Drop files or folders here"
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
          >
            Drop files or folders here - folders keep their paths.
          </div>
          <p className="note">
            Limits: {size(UPLOAD_MAX_BYTES)} a file, {UPLOAD_MAX_FILES} files
            and {size(UPLOAD_MAX_TOTAL_BYTES)} a queue.
          </p>
        </>
      )}
      {refusal && (
        <div className="notice error" role="alert">
          {refusal}
        </div>
      )}
      <p className="note" role="note">
        Each file is published in one step, so the server shows the old file or
        the whole new one. But a cancellation or a timeout while a file is in
        flight can leave its outcome unknown: it may have been written. Nothing
        is retried automatically - list the directory before uploading it again.
      </p>
      {items.length > 0 && (
        <div className="table-scroll" tabIndex={0} aria-label="Upload queue">
          <table>
            <thead>
              <tr>
                <th>file</th>
                <th>size</th>
                <th>status</th>
                <th>note</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} data-status={item.status}>
                  <td className="file-name">{item.relative}</td>
                  <td>{size(item.file.size)}</td>
                  <td>
                    {STATUS[item.status]}
                    {item.status === "uploading" && (
                      <>
                        {" "}
                        <progress
                          max={1}
                          value={item.progress}
                          aria-label={`Progress of ${item.relative}`}
                        />
                      </>
                    )}
                  </td>
                  <td>{item.message}</td>
                  <td>
                    {!running &&
                      (item.status === "queued" ||
                        item.status === "invalid") && (
                        <button
                          type="button"
                          data-activity="files.upload.remove"
                          aria-label={`Remove ${item.relative}`}
                          onClick={() =>
                            setItems((current) =>
                              current.filter((other) => other.id !== item.id),
                            )
                          }
                        >
                          Remove
                        </button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {running && (
        <p role="status">
          Uploading {position.at} of {position.of}…
        </p>
      )}
      {push && (
        <p className="note" aria-label="Push">
          Push <code>{push.id}</code> - the server keeps what each file
          replaced, so it can be diffed and restored from the push history.
          {push.pruned > 0 &&
            ` Retention pruned ${push.pruned} older ${push.pruned === 1 ? "push" : "pushes"}.`}
        </p>
      )}
      {closeProblem && (
        <div className="notice error" role="alert">
          {closeProblem}
        </div>
      )}
      <div className="actions">
        <label>
          <input
            type="checkbox"
            data-activity="files.upload.overwrite"
            checked={overwrite}
            disabled={running}
            onChange={(event) => setOverwrite(event.target.checked)}
          />{" "}
          Replace existing files
        </label>
        {!running && !confirming && (
          <button
            type="button"
            data-activity="files.upload.review"
            disabled={queued.length === 0 || !canUpload}
            onClick={() => setConfirming(true)}
          >
            Upload {queued.length} {queued.length === 1 ? "file" : "files"}…
          </button>
        )}
        {running && (
          <button
            type="button"
            data-activity="files.upload.cancel"
            onClick={() => controller.current?.abort()}
          >
            Cancel
          </button>
        )}
        {!running && finished && (
          <button
            type="button"
            data-activity="files.upload.clear"
            onClick={clearFinished}
          >
            Clear finished
          </button>
        )}
      </div>
      {confirming && !running && (
        <div className="notice" role="group" aria-label="Confirm upload">
          <p>
            Upload {queued.length} {queued.length === 1 ? "file" : "files"} (
            {size(queuedBytes)}) to <strong>{target}</strong> at{" "}
            <code>
              {dest}/{configDir}/
            </code>
            ?
          </p>
          <p>
            {overwrite
              ? "Existing files with the same path WILL BE REPLACED (their permissions are kept)."
              : "Existing files are left alone: an upload whose path exists is refused."}
          </p>
          <button
            type="button"
            data-activity="files.upload.confirm"
            onClick={start}
          >
            Confirm upload
          </button>{" "}
          <button
            type="button"
            data-activity="files.upload.back"
            onClick={() => setConfirming(false)}
          >
            Back
          </button>
        </div>
      )}
    </section>
  );
}
