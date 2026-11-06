// /me/capabilities - models/access.py.

import type { Pollable } from "./client";

/** One feature and action the access policy grants - models.Capability. */
export interface Capability {
  feature: string;
  action: "read" | "write";
}
/** GET /me/capabilities (#2045): who the caller is, as a trusted proxy
 *  vouches, and what the server's access policy lets them do. The browser
 *  hides what is not here; the server refuses it either way. */
export interface Capabilities extends Pollable {
  user: string;
  authenticated: boolean;
  groups: string[];
  capabilities: Capability[];
  features: Record<string, string[]>;
  /** the inspection policy the server enforces (#2032) */
  inspection: Inspection;
}
/** Inspection mode as the server enforces it - models.InspectionOut (#2032).
 *  The API and uqs refuse the writes; an ssh tunnel is not what makes this
 *  read-only, and nothing in the UI may say it is. */
export interface Inspection {
  /** the whole frontend: every write refused */
  active: boolean;
  /** declared targets in inspection mode: every write naming one refused */
  targets: string[];
  /** which q login the frontend's own reads use */
  q_login: "inspection" | "service";
  /** why the target registry could not be read, when it could not */
  registry_error: string | null;
}
