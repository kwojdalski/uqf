/**
 * The admin Settings view (#2046): the access policy and the runtime
 * settings, edited in forms react-jsonschema-form renders from the JSON
 * schemas GET /settings sends - this file keeps no list of fields of its own.
 *
 * Loaded lazily (App.tsx), so the form library and its validator cost
 * nothing until someone opens the view.
 *
 * Nothing here enforces anything. The server refuses a change the caller's
 * policy does not grant, a stale one, one that would leave no administrator,
 * and anything its own models do not accept - whatever this form allowed.
 */
import { useLayoutEffect, useRef, useState } from "react";
import Form from "@rjsf/core";
import type { IChangeEvent } from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { mutate, writeToken } from "./api/client";
import type {
  AccessChangeBody,
  FormData,
  RuntimeChangeBody,
  SettingChange,
  SettingsChangeResult,
  SettingsData,
} from "./api/settings";
import { INTERACTIVE, tracked, triggerOf } from "./activity";
import { useCapabilities } from "./capabilities";
import { useResource } from "./useResource";

/** A name the server keeps as an activity target (activity._TARGET). */
const UNSAFE = /[^A-Za-z0-9_.:/@ ,+-]/g;

/** Name every control the form library renders, which no source line can:
 *  `<prefix>.<field>`, from the id RJSF gives it with list indices dropped,
 *  so `root_grants_3_subject` is recorded as `settings.access.grants.subject`. */
function nameControls(root: HTMLElement, prefix: string) {
  for (const element of root.querySelectorAll(INTERACTIVE)) {
    if (element.hasAttribute("data-activity")) continue;
    const id =
      element.getAttribute("type") === "submit"
        ? "save"
        : (element.id || element.getAttribute("name") || "")
            .replace(/^root_?/, "")
            .replace(/_\d+(?=_|$)/g, ".")
            .replace(/\._*/g, ".")
            .replace(/__+/g, ".")
            .replace(/[._]+$/, "");
    const label =
      id ||
      element.getAttribute("title") ||
      element.getAttribute("aria-label") ||
      element.tagName.toLowerCase();
    element.setAttribute(
      "data-activity",
      `${prefix}.${label}`.replace(UNSAFE, "-").slice(0, 200),
    );
  }
}

function useNamedControls(prefix: string) {
  const ref = useRef<HTMLDivElement | null>(null);
  // A layout effect, so the controls are named in the commit that shows them:
  // a passive effect can run after the form is visible, and a click in between
  // would be recorded without a name (as #2716 was for the permission).
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    nameControls(root, prefix);
    const observer = new MutationObserver(() => nameControls(root, prefix));
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [prefix]);
  return ref;
}

function Changes({ changes }: { changes: SettingChange[] }) {
  if (!changes.length) return <p className="muted">Nothing changed.</p>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Field</th>
          <th>Before</th>
          <th>After</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c, i) => (
          <tr key={i}>
            <td>{c.field}</td>
            <td className="mono">{c.before ?? "—"}</td>
            <td className="mono">{c.after ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One editable section: a schema form, saved with the version it was
 *  edited from so a change made elsewhere since is refused, not lost. */
function SettingsForm({
  name,
  title,
  schema,
  data,
  version,
  disabled,
  why,
  save,
  onSaved,
}: {
  name: "access" | "runtime";
  title: string;
  schema: FormData;
  data: FormData;
  version: string;
  disabled: boolean;
  why: string;
  save: (version: string, data: FormData) => Promise<SettingsChangeResult>;
  onSaved: () => void;
}) {
  // The draft and the version it started from, kept while the operator
  // edits - a poll that brings a newer version must not replace their work.
  const [draft, setDraft] = useState<{ data: FormData; from: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SettingChange[] | null>(null);
  const ref = useNamedControls(`settings.${name}`);
  const stale = draft !== null && draft.from !== version;

  async function submit(event: IChangeEvent<FormData>) {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const out = await save(draft?.from ?? version, event.formData ?? data);
      setResult(out.changes);
      setDraft(null);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>{title}</h2>
      {disabled && <p className="sidebar-note">{why}</p>}
      {stale && (
        <div className="notice pending" role="status">
          This changed on the server since you started editing. Saving will be
          refused; discard your edits to load the current version.
        </div>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {result && <Changes changes={result} />}
      <div ref={ref} className="settings-form">
        <Form
          schema={schema as RJSFSchema}
          formData={draft?.data ?? data}
          validator={validator}
          disabled={disabled || busy}
          onChange={(event) =>
            setDraft((d) => ({
              data: event.formData as FormData,
              from: d?.from ?? version,
            }))
          }
          onSubmit={submit}
          uiSchema={{
            "ui:submitButtonOptions": {
              submitText: busy ? "Saving…" : `Save ${title.toLowerCase()}`,
              norender: disabled,
            },
          }}
        />
      </div>
      {draft && (
        <button
          type="button"
          data-activity={`settings.${name}.discard`}
          onClick={() => {
            setDraft(null);
            setError("");
          }}
        >
          Discard edits
        </button>
      )}
    </section>
  );
}

export default function SettingsView() {
  const access = useCapabilities();
  const settings = useResource<SettingsData>("/settings");
  const [token, setToken] = useState(writeToken.get());
  const data = settings.data;
  if (settings.error && !data)
    return (
      <div className="notice error" role="alert">
        <strong>Could not load the settings</strong>
        <p>{settings.error.message}</p>
      </div>
    );
  if (!data) return <p>Loading…</p>;

  // What the form shows as disabled, and why - the server's reasons, said
  // first; the policy's last, since only the server can know it for sure.
  const mayWrite = access.can("settings", "write");
  const reason = (editable: boolean, variable: string) =>
    !data.writes_enabled
      ? "Writes are off on this server (UQF_FRONTEND_ENABLE_WRITES)."
      : !editable
        ? `Not editable here: set ${variable} on the server.`
        : !mayWrite
          ? "Your access does not include settings:write; the server would refuse a change."
          : "";
  const accessWhy = reason(data.access_editable, "UQF_FRONTEND_POLICY_FILE");
  const runtimeWhy = reason(
    data.runtime_editable,
    "UQF_FRONTEND_SETTINGS_FILE",
  );

  function save<T>(path: string, action: string, body: T) {
    return tracked(
      "submit",
      action,
      { subject: path, trigger: triggerOf() },
      (correlation) =>
        mutate<SettingsChangeResult>(path, body, "PUT", correlation),
      () => "succeeded",
    );
  }

  return (
    <>
      {settings.error && (
        <div className="notice pending" role="status">
          Showing the last settings loaded: {settings.error.message}
        </div>
      )}
      <section className="panel">
        <h2>Who can change these</h2>
        <p>
          {data.administrators.length
            ? data.administrators
                .map((a) => (a === "*" ? "everyone" : a))
                .join(", ")
            : "Nobody this deployment can identify."}
        </p>
        {data.access_error && (
          <p role="alert" className="error-text">
            The policy file&rsquo;s last edit on disk was not taken:{" "}
            {data.access_error}
          </p>
        )}
        {data.writes_enabled && mayWrite && (
          <>
            <label>
              Write token
              <input
                data-activity="settings.token"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => {
                  setToken(e.target.value);
                  writeToken.set(e.target.value);
                }}
              />
            </label>
            <p className="sidebar-note">
              The server&rsquo;s <code>UQF_FRONTEND_WRITE_TOKEN</code>, sent
              with every change. Kept in this tab only.
            </p>
          </>
        )}
      </section>
      <SettingsForm
        name="access"
        title="Access policy"
        schema={data.schemas.access}
        data={data.access}
        version={data.access_version}
        disabled={accessWhy !== ""}
        why={accessWhy}
        save={(version, policy) =>
          save<AccessChangeBody>("/settings/access", "settings.access", {
            version,
            policy,
          })
        }
        onSaved={settings.refresh}
      />
      <SettingsForm
        name="runtime"
        title="Runtime settings"
        schema={data.schemas.runtime}
        data={data.runtime}
        version={data.runtime_version}
        disabled={runtimeWhy !== ""}
        why={runtimeWhy}
        save={(version, values) =>
          save<RuntimeChangeBody>("/settings/runtime", "settings.runtime", {
            version,
            settings: values,
          })
        }
        onSaved={settings.refresh}
      />
      <section className="panel">
        <h2>Secrets</h2>
        <p className="sidebar-note">
          Set in the server&rsquo;s environment, never here, and never sent to
          the browser: only whether each is set.
        </p>
        <table className="data-table">
          <tbody>
            {data.secrets.map((s) => (
              <tr key={s.name}>
                <td className="mono">{s.name}</td>
                <td>{s.configured ? "Set" : "Not set"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
