// /settings (#2046) - models/settings.py.

import type { Pollable } from "./client";

/** A form's data, shaped by the JSON schema the server sends with it: the
 *  browser keeps no list of its own of the fields (#2046). */
export type FormData = Record<string, unknown>;
/** A setting that holds a secret - models.SecretSetting. Never its value. */
export interface SecretSetting {
  name: string;
  configured: boolean;
}
/** One line of a settings change's diff - models.SettingChange. */
export interface SettingChange {
  field: string;
  before: string | null;
  after: string | null;
}
/** GET /settings (#2046) - models.SettingsResponse. */
export interface SettingsData extends Pollable {
  access: FormData;
  access_version: string;
  access_editable: boolean;
  access_error: string | null;
  administrators: string[];
  runtime: FormData;
  runtime_version: string;
  runtime_editable: boolean;
  writes_enabled: boolean;
  secrets: SecretSetting[];
  schemas: Record<"access" | "runtime", FormData>;
}
/** PUT /settings/access - models.AccessChangeRequest. */
export interface AccessChangeBody {
  version: string;
  policy: FormData;
}
/** PUT /settings/runtime - models.RuntimeChangeRequest. */
export interface RuntimeChangeBody {
  version: string;
  settings: FormData;
}
/** What a settings change did - models.SettingsChangeResponse. */
export interface SettingsChangeResult {
  changes: SettingChange[];
  version: string;
}
