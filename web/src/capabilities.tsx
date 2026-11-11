import {
  createContext,
  useContext,
  useLayoutEffect,
  type ReactNode,
} from "react";
import type { Capabilities, Inspection } from "./api/access";
import { allowRecording } from "./activity";
import { useResource } from "./useResource";

/** The feature each view belongs to - the server's access.FEATURES. A view
 *  missing here is always shown: the server still decides. */
export const VIEW_FEATURE: Readonly<Record<string, string>> = {
  Coverage: "coverage",
  Backfills: "backfills",
  Desk: "desk",
  Charts: "charts",
  Compare: "compare",
  Fleet: "fleet",
  Queue: "queue",
  Connections: "connections",
  Usage: "usage",
  Services: "services",
  Control: "control",
  Activity: "activity",
  Settings: "settings",
};

export interface Access {
  /** True once the server has said what this caller may do. */
  known: boolean;
  user: string | null;
  authenticated: boolean;
  groups: readonly string[];
  /** Whether the server's policy grants `feature:action`. */
  can(feature: string, action: "read" | "write"): boolean;
  /** Whether a view's feature is readable. */
  canView(view: string): boolean;
  /** The inspection policy the server enforces (#2032), or null until it
   *  says. Shown, never enforced here: the server refuses the writes. */
  inspection: Inspection | null;
}

/** Until the server answers - or when it answers with something that is not
 *  a policy - everything is shown. That is not a gap: hiding a control
 *  refuses nothing, and the server refuses whatever its policy does not grant
 *  whether or not the button was on screen. */
const UNKNOWN: Access = {
  known: false,
  user: null,
  authenticated: false,
  groups: [],
  can: () => true,
  canView: () => true,
  inspection: null,
};

function fromServer(data: Capabilities | undefined): Access {
  if (!data || !Array.isArray(data.capabilities)) return UNKNOWN;
  const granted = new Set(
    data.capabilities.map((c) => `${c.feature}:${c.action}`),
  );
  const can = (feature: string, action: "read" | "write") =>
    granted.has(`${feature}:${action}`);
  return {
    known: true,
    user: data.user,
    authenticated: data.authenticated,
    groups: data.groups,
    can,
    canView: (view) =>
      VIEW_FEATURE[view] === undefined || can(VIEW_FEATURE[view], "read"),
    inspection: data.inspection ?? null,
  };
}

const AccessContext = createContext<Access>(UNKNOWN);

/** Asks GET /me/capabilities once for the whole app, and again at the
 *  server's cadence so a policy change reaches an open tab (#2045). */
export function CapabilitiesProvider({ children }: { children: ReactNode }) {
  const resource = useResource<Capabilities>("/me/capabilities");
  const access = fromServer(resource.data);
  const recording = !access.known || access.can("activity", "write");
  // A caller the policy does not let record its history sends none, rather
  // than having every batch refused. A layout effect, so the permission holds
  // from the commit that shows the policy's views: a passive effect can run
  // after that page is visible, and a click in between would be queued (#2716).
  useLayoutEffect(() => allowRecording(recording), [recording]);
  return (
    <AccessContext.Provider value={access}>{children}</AccessContext.Provider>
  );
}

/** What this caller may do, for showing and hiding - never for enforcing. */
export function useCapabilities(): Access {
  return useContext(AccessContext);
}

/** The inspection-mode banner (#2032): what the server refuses, as it says.
 *  It never says the ssh tunnel is read-only - it is not: the API and uqs
 *  refuse changes, and anyone with ssh access to the server still can. */
export function InspectionBanner({ inspection }: { inspection: Inspection }) {
  const targets = inspection.targets;
  if (!inspection.active && targets.length === 0 && !inspection.registry_error)
    return null;
  return (
    <div
      className="notice inspection"
      role="status"
      aria-label="Inspection mode"
    >
      <strong>Inspection mode</strong>
      {inspection.active ? (
        <p>
          The server refuses every change from this frontend: starting, stopping
          and restarting processes, backfills, uploads, deletes, config pushes
          and restores, and settings. Reads stay available.
        </p>
      ) : targets.length > 0 ? (
        <p>
          The server refuses every change to{" "}
          {targets.length === 1 ? "target" : "targets"}{" "}
          <code>{targets.join(", ")}</code>: uploads, deletes, config pushes and
          restores. Reads stay available.
        </p>
      ) : null}
      {inspection.registry_error && (
        <p>
          The deployment registry cannot be read, so every change naming a
          target is refused: {inspection.registry_error}
        </p>
      )}
      <p>
        Enforced by the frontend API and by <code>uqs</code>, not by the ssh
        tunnel: ssh access to the server is not read-only.
        {inspection.active &&
          (inspection.q_login === "inspection"
            ? " The frontend reads the gateway as a restricted q login."
            : " The frontend reads the gateway as its ordinary service login; no restricted q login is configured.")}
      </p>
    </div>
  );
}
