import { useEffect, useState } from "react";
import { FileTable } from "./FileTable";
import {
  ApiError,
  type RemoteEntry,
  type RemoteFiles,
  remoteFilesPath,
  request,
  writeToken,
} from "./api";

/** The fields of the release's deploy report worth a glance, in order. */
const REPORT_FIELDS = [
  "revision",
  "status",
  "stage",
  "profile",
  "previous_release",
  "error",
];

/** One directory under a registered target's deployment root (#1950).
 *
 *  Each listing is an ssh round trip the API makes with the release's own
 *  Python, so nothing here polls: a listing is fetched when the target or
 *  directory changes, and again only on Refresh. It reads; nothing on this
 *  page deploys, starts or stops anything. Links and special files are shown
 *  blocked and cannot be opened - the API refuses them too. The parent
 *  renders it with `key={target}`, so another target starts at its root. */
export function FileBrowser({
  target,
  onClose,
}: {
  target: string;
  onClose: () => void;
}) {
  const [path, setPath] = useState("");
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    data?: RemoteFiles;
    error?: ApiError;
    loading: boolean;
  }>({ loading: true });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState((previous) => ({ ...previous, loading: true }));
    const token = writeToken.get();
    request<RemoteFiles>(
      remoteFilesPath(target, path),
      controller.signal,
      undefined,
      "GET",
      token ? `Bearer ${token}` : undefined,
    )
      .then((data) => {
        if (active) setState({ data, loading: false });
      })
      .catch((error: unknown) => {
        if (!active) return;
        const failure =
          error instanceof ApiError ? error : new ApiError(String(error));
        // Keep the listing that was shown only if it is still this directory.
        setState((previous) => ({
          data:
            previous.data?.target === target && previous.data.path === path
              ? previous.data
              : undefined,
          error: failure,
          loading: false,
        }));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [target, path, revision]);

  const data = state.data;
  const crumbs = path ? path.split("/") : [];
  const release = data?.release;
  return (
    <section className="panel" aria-label={`Files on ${target}`}>
      <div className="section-heading">
        <h2>Files on {target}</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {data && (
        <p className="note">
          Deployment root: <code>{data.root}</code>
        </p>
      )}
      <nav aria-label="Path" className="crumbs">
        <button type="button" onClick={() => setPath("")}>
          root
        </button>
        {crumbs.map((crumb, index) => (
          <span key={index}>
            {" / "}
            <button
              type="button"
              onClick={() => setPath(crumbs.slice(0, index + 1).join("/"))}
            >
              {crumb}
            </button>
          </span>
        ))}
      </nav>
      <div className="refresh">
        <span role="status">{state.loading ? "Loading…" : ""}</span>
        <button
          type="button"
          disabled={!path}
          onClick={() => setPath(crumbs.slice(0, -1).join("/"))}
        >
          Up
        </button>
        <button type="button" onClick={() => setRevision((r) => r + 1)}>
          Refresh
        </button>
      </div>
      {state.error && (
        <div className="notice error" role="alert">
          <strong>Could not list this directory</strong>
          <p>{state.error.message}</p>
          {state.error.status === 401 && (
            <p>Enter the write token in the Control view, then refresh.</p>
          )}
        </div>
      )}
      {release && (
        <dl className="summary" aria-label="Release">
          <div>
            <dt>release</dt>
            <dd>{release.release ?? release.current ?? "none"}</dd>
          </div>
          {REPORT_FIELDS.filter(
            (key) =>
              release.report?.[key] !== undefined &&
              release.report?.[key] !== null &&
              release.report?.[key] !== "",
          ).map((key) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd>{String(release.report?.[key])}</dd>
            </div>
          ))}
        </dl>
      )}
      {release?.report_error && (
        <p className="note">Deployment report: {release.report_error}</p>
      )}
      {data &&
        (data.entries.length === 0 ? (
          <p className="empty">This directory is empty.</p>
        ) : (
          <FileTable
            key={`${target}:${path}`}
            entries={data.entries}
            disabled={
              state.loading || data.path !== path || Boolean(state.error)
            }
            onOpen={(name) =>
              setPath(data.path ? `${data.path}/${name}` : name)
            }
          />
        ))}
      {data?.truncated && (
        <p className="note">
          Showing the first {data.entries.length} of{" "}
          {data.total ?? "too many to count"} entries.
        </p>
      )}
      <p className="note">
        Read only, over ssh with the release&apos;s own Python. Links and
        special files are not followed.
      </p>
    </section>
  );
}
