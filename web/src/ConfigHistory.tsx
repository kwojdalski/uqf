import { useState } from "react";
import {
  ApiError,
  type ConfigDiffFile,
  type ConfigHistory as History,
  type ConfigPushDetail,
  type ConfigPushFile,
  type ConfigRestore,
  type HistoryOps,
  historyOps,
} from "./api";
import { size } from "./FileTable";

const ACTION: Record<ConfigPushFile["action"], string> = {
  create: "created",
  replace: "replaced",
  delete: "deleted",
};

const NOW: Record<string, string> = {
  after: "as the push left it",
  before: "as before the push",
  changed: "changed since",
  blocked: "cannot be read",
};

const STEP: Record<string, string> = {
  write: "write its earlier content back",
  delete: "delete it (the push created it)",
  unchanged: "nothing - already as before",
  conflict: "CONFLICT - changed since the push",
};

/** The first 12 hex digits of a sha256, or a dash for none. */
export function shortHash(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "-";
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Before against after for one file: the diff, or why there is none. */
function DiffView({ file, cap }: { file: ConfigDiffFile; cap: number }) {
  if (file.kind === "text")
    return (
      <pre className="diff" aria-label={`Diff of ${file.path}`}>
        {file.diff || "(no change)"}
      </pre>
    );
  const why = {
    binary: "binary - not diffed",
    oversized: `larger than ${size(cap)} - not diffed`,
    omitted: "past what one answer carries - diff this file alone",
    missing: "its history copy is missing",
  }[file.kind];
  return (
    <p className="note" aria-label={`Diff of ${file.path}`}>
      {why}: {size(file.previous_size ?? 0)} ({shortHash(file.previous_sha256)})
      → {size(file.size ?? 0)} ({shortHash(file.sha256)})
    </p>
  );
}

/** A target's config push history (#2002): each push, its files with their
 *  hashes, a diff fetched on demand, and a restore - itself a confirmed push.
 *
 *  Everything is read from the server's history over ssh, so nothing is
 *  fetched until asked for and nothing polls. File contents are shown here,
 *  in a diff the operator opened, and never recorded anywhere else. */
export function ConfigHistory({
  target,
  ops = historyOps,
  onRestored,
}: {
  target: string;
  /** Injectable for tests; the real ones call /config/pushes. */
  ops?: HistoryOps;
  onRestored: () => void;
}) {
  const [history, setHistory] = useState<History>();
  const [detail, setDetail] = useState<ConfigPushDetail>();
  const [diffs, setDiffs] = useState<Record<string, ConfigDiffFile>>({});
  const [diffCap, setDiffCap] = useState(0);
  const [plan, setPlan] = useState<ConfigRestore>();
  const [restored, setRestored] = useState<ConfigRestore>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run<T>(work: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await work();
    } catch (failure: unknown) {
      setError(message(failure));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function load() {
    const listed = await run(() => ops.list(target));
    if (listed) setHistory(listed);
  }

  async function open(push: string) {
    setPlan(undefined);
    setRestored(undefined);
    setDiffs({});
    const shown = await run(() => ops.show(target, push));
    if (shown) setDetail(shown);
  }

  async function diff(push: string, path: string) {
    const answer = await run(() => ops.diff(target, push, path));
    if (!answer) return;
    setDiffCap(answer.diff_cap);
    setDiffs((current) => ({
      ...current,
      ...Object.fromEntries(answer.files.map((f) => [f.path, f])),
    }));
  }

  async function review(push: string) {
    setRestored(undefined);
    const answer = await run(() => ops.plan(target, push));
    if (answer) setPlan(answer);
  }

  async function restore(push: string) {
    setBusy(true);
    setError("");
    try {
      const answer = await ops.restore(target, push);
      setRestored(answer);
      setPlan(undefined);
      onRestored();
      const listed = await ops.list(target);
      setHistory(listed);
    } catch (failure: unknown) {
      // a refusal (4xx) wrote nothing; anything else may have written some
      const certain =
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500;
      setError(
        certain
          ? `Restore refused - nothing was written: ${message(failure)}`
          : `Restore outcome uncertain: ${message(failure)}. Some files may have been restored - reload the history before restoring again; nothing is retried automatically.`,
      );
    } finally {
      setBusy(false);
    }
  }

  const conflicts = plan?.plan.filter((p) => p.step === "conflict") ?? [];
  const steps =
    plan?.plan.filter((p) => p.step === "write" || p.step === "delete") ?? [];
  return (
    <section className="panel" aria-label="Config push history">
      <div className="section-heading">
        <h3>Config push history on {target}</h3>
        <button
          type="button"
          data-activity="config.history.load"
          disabled={busy}
          onClick={load}
        >
          {history ? "Reload history" : "Show push history"}
        </button>
      </div>
      <p className="note">
        Each push keeps, on the server only, what it replaced and what it wrote,
        under <code>shared/config/.uqf-history/</code>. Old pushes are pruned to
        the configured bound.
      </p>
      {busy && <p role="status">Reading the server&apos;s history…</p>}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {history &&
        (history.pushes.length === 0 ? (
          <p className="empty">No pushes recorded.</p>
        ) : (
          <div className="table-scroll" tabIndex={0} aria-label="Pushes">
            <table>
              <thead>
                <tr>
                  <th>push</th>
                  <th>when</th>
                  <th>by</th>
                  <th>what</th>
                  <th>files</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {history.pushes.map((p) => (
                  <tr key={p.push_id}>
                    <td>
                      <code>{p.push_id}</code>
                    </td>
                    <td>{p.created ?? ""}</td>
                    <td>{p.user ?? "unknown"}</td>
                    <td>
                      {p.error
                        ? `unreadable: ${p.error}`
                        : p.kind === "restore"
                          ? `restore of ${p.restores}`
                          : `push to ${p.destination}`}
                    </td>
                    <td>
                      {p.file_count ?? 0} ({size(p.total_bytes ?? 0)})
                      {p.states?.pending ? `, ${p.states.pending} pending` : ""}
                      {p.states?.failed ? `, ${p.states.failed} failed` : ""}
                    </td>
                    <td>
                      <button
                        type="button"
                        data-activity="config.history.show"
                        aria-label={`Show push ${p.push_id}`}
                        disabled={busy || Boolean(p.error)}
                        onClick={() => open(p.push_id)}
                      >
                        Show
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      {history && history.pruned.length > 0 && (
        <p className="note" aria-label="Pruned pushes">
          Pruned:{" "}
          {history.pruned
            .map((p) => `${p.push_id} (${p.why}, ${p.pruned_at})`)
            .join("; ")}
        </p>
      )}
      {detail && (
        <section aria-label={`Push ${detail.push.push_id}`}>
          <h4>
            Push <code>{detail.push.push_id}</code>
            {detail.push.kind === "restore" &&
              ` - a restore of ${detail.push.restores}`}
          </h4>
          <div className="table-scroll" tabIndex={0} aria-label="Pushed files">
            <table>
              <thead>
                <tr>
                  <th>file</th>
                  <th>change</th>
                  <th>old sha256</th>
                  <th>new sha256</th>
                  <th>state</th>
                  <th>now</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {detail.push.files.map((f) => (
                  <tr key={f.path} data-state={f.state}>
                    <td className="file-name">{f.path}</td>
                    <td>{ACTION[f.action]}</td>
                    <td>
                      <code title={f.previous_sha256 ?? "none"}>
                        {shortHash(f.previous_sha256)}
                      </code>
                    </td>
                    <td>
                      <code title={f.sha256 ?? "none"}>
                        {shortHash(f.sha256)}
                      </code>
                    </td>
                    <td>
                      {f.state}
                      {f.error ? ` (${f.error})` : ""}
                    </td>
                    <td>{f.now ? NOW[f.now] : ""}</td>
                    <td>
                      {f.state !== "failed" && (
                        <button
                          type="button"
                          data-activity="config.history.diff"
                          aria-label={`Diff ${f.path}`}
                          disabled={busy}
                          onClick={() => diff(detail.push.push_id, f.path)}
                        >
                          Diff
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {detail.push.reported.length > 0 && (
            <p className="note">
              Never recorded by the server:{" "}
              {detail.push.reported
                .map((r) => `${r.path} (${r.outcome})`)
                .join("; ")}
            </p>
          )}
          {Object.values(diffs).map((file) => (
            <DiffView key={file.path} file={file} cap={diffCap} />
          ))}
          <div className="actions">
            <button
              type="button"
              data-activity="config.restore.review"
              disabled={busy}
              onClick={() => review(detail.push.push_id)}
            >
              Restore…
            </button>
          </div>
        </section>
      )}
      {plan && (
        <div className="notice" role="group" aria-label="Confirm restore">
          <p>
            Restore push <code>{plan.source_push_id}</code> on{" "}
            <strong>{target}</strong>? Each file goes back to how it was before
            that push, as a new push of its own:
          </p>
          <ul aria-label="Restore plan">
            {plan.plan.map((p) => (
              <li key={p.path}>
                <code>{JSON.stringify(p.path)}</code>: {STEP[p.step]}
                {p.error ? ` (${p.error})` : ""}
              </li>
            ))}
          </ul>
          {conflicts.length > 0 ? (
            <p role="alert">
              {conflicts.length} file(s) changed since the push: restoring would
              overwrite those changes, so it is refused.
            </p>
          ) : steps.length === 0 ? (
            <p>Nothing to restore - every file is already as it was.</p>
          ) : (
            <button
              type="button"
              data-activity="config.restore.confirm"
              disabled={busy}
              onClick={() => restore(plan.source_push_id)}
            >
              Confirm restore of {steps.length}{" "}
              {steps.length === 1 ? "file" : "files"}
            </button>
          )}{" "}
          <button
            type="button"
            data-activity="config.restore.back"
            onClick={() => setPlan(undefined)}
          >
            Back
          </button>
        </div>
      )}
      {restored && (
        <div className="notice" role="status" aria-label="Restore results">
          <p>
            Restored as push <code>{restored.push_id}</code>:
          </p>
          <ul>
            {restored.results.map((r) => (
              <li key={r.path}>
                <code>{JSON.stringify(r.path)}</code>:{" "}
                {r.outcome === "succeeded"
                  ? r.step === "delete"
                    ? "deleted"
                    : "restored"
                  : `failed - ${r.error}`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
