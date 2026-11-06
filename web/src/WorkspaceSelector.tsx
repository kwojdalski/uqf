/**
 * The workspace selector (#2029): which stack this tab's Desk, Fleet,
 * Connections, Queue and Usage views read - `local`, or a target the server's
 * uqs registry declares. The list is GET /workspaces; whether the chosen one
 * can be reached now is GET /workspace, asked for that workspace like every
 * other read. A target that cannot be reached is shown as an error naming it:
 * the views stay empty rather than showing the local stack in its place.
 */
import { useEffect } from "react";
import type { Workspaces, WorkspaceStatus } from "./api/services";
import { useCapabilities } from "./capabilities";
import { useResource } from "./useResource";
import { LOCAL, selectWorkspace, useWorkspace } from "./workspace";

/** Which workspace a result came from, beside the result itself. */
export function TargetLabel({ target }: { target?: string }) {
  if (!target) return null;
  return (
    <span className="target-label" title="The workspace this result came from">
      {target === LOCAL ? "Local stack" : `Target ${target}`}
    </span>
  );
}

export function WorkspaceSelector() {
  const access = useCapabilities();
  const allowed = access.can("services", "read");
  const workspace = useWorkspace();
  // A caller who may not read the registry reads the local stack only.
  useEffect(() => {
    if (access.known && !allowed) selectWorkspace(LOCAL);
  }, [access.known, allowed]);
  const list = useResource<Workspaces>(allowed ? "/workspaces" : null);
  const status = useResource<WorkspaceStatus>(allowed ? "/workspace" : null);
  const known = list.data?.workspaces ?? [];
  const chosen = known.find((item) => item.target === workspace);
  const state = !allowed
    ? "Local only"
    : status.error
      ? "Unavailable"
      : status.data?.target === workspace
        ? "Connected"
        : "Connecting";
  return (
    <div className="workspace-selector" role="group" aria-label="Workspace">
      <label>
        Workspace{" "}
        <select
          data-activity="workspace.select"
          value={workspace}
          disabled={!allowed}
          onChange={(event) => selectWorkspace(event.target.value)}
        >
          {/* The current choice stays listed even before the list arrives,
              or if the registry no longer has it - then it says so. */}
          {!chosen && <option value={workspace}>{workspace}</option>}
          {known.map((item) => (
            <option
              key={item.target}
              value={item.target}
              disabled={!item.available && item.target !== workspace}
              title={item.reason ?? item.ssh ?? undefined}
            >
              {item.target}
              {item.environment ? ` (${item.environment})` : ""}
              {item.available ? "" : " - unavailable"}
            </option>
          ))}
        </select>
      </label>
      <span
        className={`badge ${state.toLowerCase().replaceAll(" ", "-")}`}
        role="status"
      >
        {state}
      </span>
      <span className="badge" title="How the server reaches this workspace">
        {chosen?.connection ?? status.data?.connection ?? "…"}
      </span>
      <span
        className="badge"
        title="What this frontend allows in this workspace - enforced by its API, not by the ssh tunnel"
      >
        {chosen?.access ?? status.data?.access ?? "…"}
      </span>
      {workspace !== LOCAL && status.error && (
        <span className="workspace-error" role="alert">
          {status.error.message}
        </span>
      )}
      {list.data?.invalid && (
        <span className="workspace-error" role="alert">
          {list.data.invalid}
        </span>
      )}
    </div>
  );
}
