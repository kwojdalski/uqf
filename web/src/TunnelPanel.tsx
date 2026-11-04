import { useEffect, useState } from "react";
import {
  ApiError,
  request,
  type Services,
  type TunnelCheck,
  type TunnelMapping,
  type TunnelPlan,
  tunnelPath,
  writeToken,
} from "./api";
import { useResource } from "./useResource";

/** GET one of a target's tunnel routes with the write token: each reaches
 *  the server over ssh with the API's key, as browsing files does. */
function fetchTunnel<T>(path: string, signal: AbortSignal): Promise<T> {
  const token = writeToken.get();
  return request<T>(
    path,
    signal,
    undefined,
    "GET",
    token ? `Bearer ${token}` : undefined,
  );
}

function failure(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(String(error));
}

/** What a mapping is, in words: uqs's own state, and whether its local
 *  port answers now. A mapping is never "connected" on the plan's word. */
export function mappingMarks(mapping: TunnelMapping): string[] {
  return [mapping.state, mapping.listening ? "listening" : "disconnected"];
}

/** The text a copy puts on the clipboard: uqs's endpoints and command as
 *  they arrived, with the marks beside them. No credential is in any of it -
 *  the plan holds none. */
export function connectionText(mapping: TunnelMapping): string {
  return [
    `${mapping.process} (${mapping.role}) [${mappingMarks(mapping).join(", ")}]`,
    `production: ${mapping.production_endpoint}`,
    `local: ${mapping.local_endpoint}`,
    `query: ${mapping.query_command}`,
  ].join("\n");
}

function CopyButton({
  text,
  label,
  activity,
}: {
  text: string;
  label: string;
  activity: string;
}) {
  const [said, setSaid] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        data-activity={activity}
        onClick={() => {
          const clipboard = navigator.clipboard;
          if (!clipboard) {
            setSaid("Clipboard unavailable - select the text instead.");
            return;
          }
          clipboard.writeText(text).then(
            () => setSaid("Copied."),
            () => setSaid("Copy failed - select the text instead."),
          );
        }}
      >
        {label}
      </button>
      {said && (
        <span role="status" className="note">
          {" "}
          {said}
        </span>
      )}
    </>
  );
}

function StateBadge({ value }: { value: string }) {
  return <span className={`badge ${value}`}>{value}</span>;
}

/** The process, its production endpoint and its local one together, with
 *  copy actions - for the Services panel and the Fleet view alike. */
export function ConnectionRows({ mappings }: { mappings: TunnelMapping[] }) {
  if (!mappings.length) return <p className="empty">Nothing to forward.</p>;
  return (
    <div className="table-scroll" tabIndex={0} aria-label="Connection details">
      <table>
        <thead>
          <tr>
            <th>process</th>
            <th>role</th>
            <th>production</th>
            <th>local</th>
            <th>state</th>
            <th>last checked</th>
            <th>copy</th>
          </tr>
        </thead>
        <tbody>
          {mappings.map((mapping) => (
            <tr key={mapping.process}>
              <td>{mapping.process}</td>
              <td>{mapping.role}</td>
              <td>
                <code>{mapping.production_endpoint}</code>
              </td>
              <td>
                <code>{mapping.local_endpoint}</code>
              </td>
              <td>
                {mappingMarks(mapping).map((mark) => (
                  <StateBadge key={mark} value={mark} />
                ))}
              </td>
              <td>{mapping.last_check?.at ?? "never"}</td>
              <td>
                <CopyButton
                  activity="tunnel.copy-endpoint"
                  label={`Copy ${mapping.process} endpoint`}
                  text={mapping.local_endpoint}
                />
                <CopyButton
                  activity="tunnel.copy-details"
                  label={`Copy ${mapping.process} details`}
                  text={connectionText(mapping)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CheckResult({ check }: { check: TunnelCheck }) {
  if (check.error)
    return (
      <div className="notice error" role="alert">
        <strong>Check failed: {check.category}</strong>
        <p>{check.error}</p>
        {check.category && check.categories[check.category] && (
          <p>{check.categories[check.category]}</p>
        )}
      </div>
    );
  return (
    <>
      <p className="note">
        Checked {check.checked_at} against release {check.release ?? "unknown"}.
      </p>
      <div className="table-scroll" tabIndex={0} aria-label="Diagnostics">
        <table>
          <thead>
            <tr>
              <th>process</th>
              <th>status</th>
              <th>identity</th>
              <th>layers</th>
              <th>last success</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {check.endpoints.map((endpoint) => (
              <tr key={endpoint.process}>
                <td>{endpoint.process}</td>
                <td>
                  <StateBadge value={endpoint.status} />
                  {endpoint.connected && <StateBadge value="connected" />}
                </td>
                <td>{endpoint.identity ?? "-"}</td>
                <td>
                  {Object.entries(endpoint.layers)
                    .map(([layer, state]) => `${layer} ${state}`)
                    .join(", ")}
                </td>
                <td>{endpoint.last_success?.at ?? "never"}</td>
                <td>
                  {endpoint.detail ||
                    (endpoint.gateway_servers
                      ? `routes to ${endpoint.gateway_servers.join(", ")}`
                      : "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** A target's tunnel plan and its check (#2030, #2031). Everything shown is
 *  uqs's: the processes and ports come from the target's release, the
 *  local endpoints and the launch command from the plan, each diagnostic
 *  from the check. Nothing is polled - each is an ssh round trip. */
export function TunnelPanel({
  target,
  onClose,
}: {
  target: string;
  onClose: () => void;
}) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [plan, setPlan] = useState<{
    data?: TunnelPlan;
    error?: ApiError;
    loading: boolean;
  }>({ loading: true });
  const [check, setCheck] = useState<{
    data?: TunnelCheck;
    error?: ApiError;
    loading: boolean;
  }>({ loading: false });
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setPlan((previous) => ({ ...previous, loading: true }));
    fetchTunnel<TunnelPlan>(tunnelPath(target, chosen), controller.signal)
      .then((data) => active && setPlan({ data, loading: false }))
      .catch(
        (error: unknown) =>
          active && setPlan({ error: failure(error), loading: false }),
      );
    return () => {
      active = false;
      controller.abort();
    };
  }, [target, chosen, revision]);

  const data = plan.data;
  const offered = data
    ? [...data.mappings.map((m) => m.process), ...data.available].sort()
    : [];
  const selected = new Set(data?.mappings.map((m) => m.process) ?? []);
  function toggle(process: string) {
    const next = new Set(selected);
    if (next.has(process)) next.delete(process);
    else next.add(process);
    if (next.size) setChosen([...next].sort());
  }
  function runCheck() {
    setCheck({ loading: true });
    fetchTunnel<TunnelCheck>(
      tunnelPath(target, chosen, true),
      new AbortController().signal,
    )
      .then((result) => {
        setCheck({ data: result, loading: false });
        setRevision((r) => r + 1); // the plan's states read the new record
      })
      .catch((error: unknown) =>
        setCheck({ error: failure(error), loading: false }),
      );
  }

  return (
    <section className="panel" aria-label={`Tunnel to ${target}`}>
      <div className="section-heading">
        <h2>Tunnel to {target}</h2>
        <button type="button" data-activity="tunnel.close" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="refresh">
        <span role="status">{plan.loading ? "Loading plan…" : ""}</span>
        <button
          type="button"
          data-activity="tunnel.refresh"
          onClick={() => setRevision((r) => r + 1)}
        >
          Refresh plan
        </button>
      </div>
      {plan.error && (
        <div className="notice error" role="alert">
          <strong>Could not plan the tunnel</strong>
          <p>{plan.error.message}</p>
          {plan.error.status === 401 && (
            <p>Enter the write token in the Control view, then refresh.</p>
          )}
        </div>
      )}
      {data && (
        <>
          <fieldset>
            <legend>Processes to forward</legend>
            {offered.map((process) => (
              <label key={process}>
                <input
                  data-activity="tunnel.process"
                  type="checkbox"
                  checked={selected.has(process)}
                  disabled={
                    plan.loading ||
                    (selected.has(process) && selected.size === 1)
                  }
                  onChange={() => toggle(process)}
                />{" "}
                {process}
              </label>
            ))}
          </fieldset>
          <ConnectionRows mappings={data.mappings} />
          <h3>Launch command</h3>
          <pre>
            <code>{data.launch_command}</code>
          </pre>
          <CopyButton
            activity="tunnel.copy-launch"
            label="Copy launch command"
            text={data.launch_command}
          />
          <p className="note">
            The command holds the ssh destination and the ports, never a
            password. Run it on this machine, then check the connections.
          </p>
          <h3>Diagnostics</h3>
          <button
            type="button"
            data-activity="tunnel.check"
            disabled={check.loading}
            onClick={runCheck}
          >
            {check.loading ? "Checking…" : "Check connections"}
          </button>
          {check.error && (
            <div className="notice error" role="alert">
              <strong>Could not run the check</strong>
              <p>{check.error.message}</p>
            </div>
          )}
          {check.data && <CheckResult check={check.data} />}
        </>
      )}
    </section>
  );
}

/** The Fleet view's copy actions: pick a target, and its tunnel plan's
 *  connection details are listed beside the local fleet (#2035). */
export function FleetConnections() {
  const services = useResource<Services>("/services");
  const [target, setTarget] = useState("");
  const [plan, setPlan] = useState<{
    data?: TunnelPlan;
    error?: ApiError;
    loading: boolean;
  }>({ loading: false });
  useEffect(() => {
    if (!target) return;
    const controller = new AbortController();
    let active = true;
    setPlan({ loading: true });
    fetchTunnel<TunnelPlan>(tunnelPath(target), controller.signal)
      .then((data) => active && setPlan({ data, loading: false }))
      .catch(
        (error: unknown) =>
          active && setPlan({ error: failure(error), loading: false }),
      );
    return () => {
      active = false;
      controller.abort();
    };
  }, [target]);
  const targets = services.data?.targets ?? [];
  if (!services.data?.remote_operations_enabled || !targets.length) return null;
  return (
    <section className="panel" aria-label="Connection details">
      <h2>Connection details</h2>
      <label>
        Deployment target{" "}
        <select
          data-activity="fleet.tunnel-target"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        >
          <option value="">Choose a target</option>
          {targets.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      {plan.loading && <p role="status">Loading plan…</p>}
      {plan.error && (
        <div className="notice error" role="alert">
          <strong>Could not plan the tunnel</strong>
          <p>{plan.error.message}</p>
        </div>
      )}
      {plan.data && <ConnectionRows mappings={plan.data.mappings} />}
    </section>
  );
}
