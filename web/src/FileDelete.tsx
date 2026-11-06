import { useState } from "react";
import { ApiError, request, writeToken } from "./api/client";
import type { RemoteDeleteResponse } from "./api/services";

export function FileDelete({
  target,
  dest,
  paths,
  disabled,
  onBusy,
  onRefresh,
}: {
  target: string;
  dest: string;
  paths: string[];
  disabled: boolean;
  onBusy: (busy: boolean) => void;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<RemoteDeleteResponse>();
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  async function remove() {
    const chosen = [...paths];
    if (
      !window.confirm(
        `Delete ${chosen.length} selected entries?\nTarget: ${target}\nDeployment root: ${dest}\nPaths relative to the deployment root:\n${chosen.map((p) => JSON.stringify(p)).join("\n")}\nDeletion is permanent. Only regular files and empty directories will be removed.`,
      )
    )
      return;
    setBusy(true);
    onBusy(true);
    setReport(undefined);
    setError("");
    setRefreshError("");
    try {
      let result: RemoteDeleteResponse;
      try {
        const token = writeToken.get();
        result = await request<RemoteDeleteResponse>(
          `/services/${encodeURIComponent(target)}/files`,
          new AbortController().signal,
          JSON.stringify({ paths: chosen, confirmed: true }),
          "DELETE",
          token ? `Bearer ${token}` : undefined,
        );
      } catch (failure) {
        setError(
          failure instanceof ApiError &&
            failure.status >= 400 &&
            failure.status < 500
            ? `Deletion refused: ${failure.message}`
            : `Deletion outcome may be unknown: ${String(failure)}. Inspect the listing before retrying.`,
        );
        return;
      }
      setReport(result);
      try {
        await onRefresh();
      } catch (failure) {
        setRefreshError(
          `Deletion results were received, but refreshing the listing failed: ${String(failure)}`,
        );
      }
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section aria-label="Delete selected entries">
      {paths.length > 0 && paths.length <= 100 && (
        <>
          <p>
            Target: <code>{target}</code>; deployment root: <code>{dest}</code>
          </p>
          <ul aria-label="Selected paths to delete">
            {paths.map((path) => (
              <li key={path}>
                <code>{JSON.stringify(path)}</code>
              </li>
            ))}
          </ul>
        </>
      )}
      <button
        type="button"
        data-activity="files.delete"
        disabled={disabled || busy || paths.length === 0 || paths.length > 100}
        onClick={remove}
      >
        {busy ? "Deleting…" : `Delete selected (${paths.length})`}
      </button>
      <p className="note">
        Delete at most 100 entries per request. Directories must be empty.
      </p>
      {error && <p role="alert">{error}</p>}
      {report && (
        <ul aria-label="Deletion results">
          {report.results.map((item, index) => (
            <li key={index}>
              <code>{JSON.stringify(item.path)}</code>:{" "}
              {item.outcome === "succeeded"
                ? "Deleted"
                : `Not deleted: ${item.error}`}
            </li>
          ))}
        </ul>
      )}
      {refreshError && <p role="alert">{refreshError}</p>}
    </section>
  );
}
