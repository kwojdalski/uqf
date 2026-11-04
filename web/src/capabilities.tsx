import { createContext, useContext, useEffect, type ReactNode } from "react";
import type { Capabilities } from "./api";
import { allowRecording } from "./activity";
import { useResource } from "./useResource";

/** The feature each view belongs to - the server's access.FEATURES. A view
 *  missing here is always shown: the server still decides. */
export const VIEW_FEATURE: Readonly<Record<string, string>> = {
  Coverage: "coverage",
  Backfills: "backfills",
  Desk: "desk",
  Charts: "desk",
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
  identity: string | null;
  authenticated: boolean;
  groups: readonly string[];
  /** Whether the server's policy grants `feature:action`. */
  can(feature: string, action: "read" | "write"): boolean;
  /** Whether a view's feature is readable. */
  canView(view: string): boolean;
}

/** Until the server answers - or when it answers with something that is not
 *  a policy - everything is shown. That is not a gap: hiding a control
 *  refuses nothing, and the server refuses whatever its policy does not grant
 *  whether or not the button was on screen. */
const UNKNOWN: Access = {
  known: false,
  identity: null,
  authenticated: false,
  groups: [],
  can: () => true,
  canView: () => true,
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
    identity: data.identity,
    authenticated: data.authenticated,
    groups: data.groups,
    can,
    canView: (view) =>
      VIEW_FEATURE[view] === undefined || can(VIEW_FEATURE[view], "read"),
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
  // than having every batch refused.
  useEffect(() => allowRecording(recording), [recording]);
  return (
    <AccessContext.Provider value={access}>{children}</AccessContext.Provider>
  );
}

/** What this caller may do, for showing and hiding - never for enforcing. */
export function useCapabilities(): Access {
  return useContext(AccessContext);
}
