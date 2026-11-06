import { useEffect, useState, type FormEvent } from "react";
import {
  type ActivityEvent,
  type ActivityExportFormat,
  type ActivityHistory,
  type ActivityLevel,
  type ActivityOutcome,
  type ActivitySource,
  type SessionLevel,
  validateTime,
} from "./api";
import {
  LEVELS,
  defaultLevel,
  effectiveLevel,
  onLevelChange,
  record,
  sessionId,
  sessionLevel,
  setSessionLevel,
  triggerOf,
} from "./activity";
import { useResource } from "./useResource";

/** What each level records, as the level select and the docs say it. */
const LEVEL_NOTES: Record<SessionLevel, string> = {
  off: "no browser history; the server still audits every write",
  actions: "navigation, selection, submit, confirm, cancel, results",
  interactions: "also every control used, by its name",
  debug: "also requests, polling and retries, rate-limited",
};
const FORMATS: { format: ActivityExportFormat; label: string }[] = [
  { format: "text", label: "Timeline (text)" },
  { format: "csv", label: "CSV" },
  { format: "jsonl", label: "JSON Lines" },
];

const OUTCOMES: ActivityOutcome[] = [
  "pending",
  "succeeded",
  "failed",
  "refused",
  "accepted",
  "unknown",
];

interface Filters {
  since: string;
  until: string;
  thisSession: boolean;
  action: string;
  target: string;
  subject: string;
  outcome: ActivityOutcome | "";
  source: ActivitySource | "";
  level: ActivityLevel | "";
  requestId: string;
}

const EMPTY: Filters = {
  since: "",
  until: "",
  thisSession: false,
  action: "",
  target: "",
  subject: "",
  outcome: "",
  source: "",
  level: "",
  requestId: "",
};

function filterParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  if (filters.thisSession) params.set("session_id", sessionId());
  if (filters.action) params.set("action", filters.action);
  if (filters.target) params.set("target", filters.target);
  if (filters.subject) params.set("subject", filters.subject);
  if (filters.outcome) params.set("outcome", filters.outcome);
  if (filters.source) params.set("source", filters.source);
  if (filters.requestId) params.set("request_id", filters.requestId);
  if (filters.level) params.set("level", filters.level);
  return params;
}

/** The query string for a set of filters: an empty field filters nothing. */
export function activityQuery(filters: Filters): string {
  const text = filterParams(filters).toString();
  return text ? `/activity?${text}` : "/activity";
}

/** The export of what the view shows: the same filters, as a file. */
export function exportQuery(
  filters: Filters,
  format: ActivityExportFormat,
): string {
  const params = filterParams(filters);
  params.set("format", format);
  return `/activity/export?${params}`;
}

/** This tab's recording level, and the operator's choice of another. */
function useLevel() {
  const read = () => ({
    chosen: sessionLevel(),
    effective: effectiveLevel(),
    server: defaultLevel(),
  });
  const [level, setLevel] = useState(read);
  useEffect(() => onLevelChange(() => setLevel(read())), []);
  return level;
}

function items(event: ActivityEvent): string {
  return event.items
    .map((item) => `${item.subject}: ${item.outcome}`)
    .join(", ");
}

/** The history: what operators did here, and what the server did when asked.
 *
 * Browser rows are intent; server rows are the audit, written for every
 * request that changes something, whoever sent it. Rows of one operation
 * share a request id - choosing one shows just that operation.
 */
export function ActivityView() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [error, setError] = useState("");
  const [format, setFormat] = useState<ActivityExportFormat>("text");
  const level = useLevel();
  const resource = useResource<ActivityHistory>(activityQuery(applied));

  function chooseLevel(value: SessionLevel | "") {
    setSessionLevel(value || null);
    // After the change, so turning a level on records that it was turned
    // on, and turning it off records nothing.
    record("select", "activity.level", {
      subject: "Activity",
      detail: { level: value || "default" },
    });
  }

  function apply(next: Filters, trigger = triggerOf()) {
    setDraft(next);
    setApplied(next);
    // The filter's shape, not its text: a target someone typed may be anything.
    record("submit", "activity.filter", {
      trigger,
      subject: "Activity",
      detail: { filter_count: Object.values(next).filter(Boolean).length },
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    try {
      if (draft.since) validateTime(draft.since);
      if (draft.until) validateTime(draft.until);
      setError("");
      apply(draft);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  const data = resource.data;
  return (
    <>
      <section className="panel" aria-label="Recording level">
        <label>
          Record this tab at
          <select
            data-activity="activity.record-level"
            value={level.chosen ?? ""}
            onChange={(e) => chooseLevel(e.target.value as SessionLevel | "")}
          >
            <option value="">
              Server default ({level.server}: {LEVEL_NOTES[level.server]})
            </option>
            {LEVELS.map((name) => (
              <option key={name} value={name}>
                {name}: {LEVEL_NOTES[name]}
              </option>
            ))}
          </select>
        </label>
        <p className="note">
          This tab records at <strong>{level.effective}</strong>. Nothing typed
          is recorded at any level: a field&apos;s change is noted, never its
          value. The server audits every write whatever the level.
        </p>
      </section>
      <form className="panel" onSubmit={submit} aria-label="Filter activity">
        <div className="fields">
          <label>
            From (UTC, timezone required)
            <input
              data-activity="activity.filter.since"
              value={draft.since}
              placeholder="e.g. 2026-11-03T00:00:00Z"
              onChange={(e) => setDraft({ ...draft, since: e.target.value })}
            />
          </label>
          <label>
            To (exclusive)
            <input
              data-activity="activity.filter.until"
              value={draft.until}
              onChange={(e) => setDraft({ ...draft, until: e.target.value })}
            />
          </label>
          <label>
            Action
            <input
              data-activity="activity.filter.action"
              value={draft.action}
              placeholder="e.g. process or process.stop"
              onChange={(e) => setDraft({ ...draft, action: e.target.value })}
            />
          </label>
          <label>
            Target
            <input
              data-activity="activity.filter.target"
              value={draft.target}
              placeholder="local, or a deployment target"
              onChange={(e) => setDraft({ ...draft, target: e.target.value })}
            />
          </label>
          <label>
            Subject
            <input
              data-activity="activity.filter.subject"
              value={draft.subject}
              placeholder="a process, a cfg key, a worker, a view"
              onChange={(e) => setDraft({ ...draft, subject: e.target.value })}
            />
          </label>
          <label>
            Outcome
            <select
              data-activity="activity.filter.outcome"
              value={draft.outcome}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  outcome: e.target.value as ActivityOutcome | "",
                })
              }
            >
              <option value="">Any</option>
              {OUTCOMES.map((outcome) => (
                <option key={outcome}>{outcome}</option>
              ))}
            </select>
          </label>
          <label>
            Source
            <select
              data-activity="activity.filter.source"
              value={draft.source}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  source: e.target.value as ActivitySource | "",
                })
              }
            >
              <option value="">Browser and server</option>
              <option value="server">Server audit only</option>
              <option value="browser">Browser activity only</option>
            </select>
          </label>
          <label>
            Level
            <select
              data-activity="activity.filter.level"
              value={draft.level}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  level: e.target.value as ActivityLevel | "",
                })
              }
            >
              <option value="">Every level</option>
              <option value="actions">actions</option>
              <option value="interactions">interactions and actions</option>
              <option value="debug">debug and everything above</option>
            </select>
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            data-activity="activity.filter.this-session"
            checked={draft.thisSession}
            onChange={(e) =>
              setDraft({ ...draft, thisSession: e.target.checked })
            }
          />
          This browser session only
        </label>
        {draft.requestId && (
          <p className="note">
            One operation: request <code>{draft.requestId}</code>
          </p>
        )}
        <div className="form-footer">
          <span>Newest first · times in UTC</span>
          <span>
            <button
              type="button"
              data-activity="activity.filter.clear"
              onClick={(e) => apply(EMPTY, triggerOf(e))}
              disabled={applied === EMPTY}
            >
              Clear
            </button>{" "}
            <button className="primary" data-activity="activity.filter.apply">
              Apply filters
            </button>
          </span>
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <div className="form-footer">
          <span>
            Export what these filters show, oldest first - the timeline groups
            each browser action under the server request it caused.
          </span>
          <span>
            <select
              aria-label="Export format"
              data-activity="activity.export.format"
              value={format}
              onChange={(e) =>
                setFormat(e.target.value as ActivityExportFormat)
              }
            >
              {FORMATS.map((item) => (
                <option key={item.format} value={item.format}>
                  {item.label}
                </option>
              ))}
            </select>{" "}
            <a
              className="button"
              data-activity="activity.export"
              href={exportQuery(applied, format)}
              download
            >
              Export
            </a>
          </span>
        </div>
      </form>
      {resource.error && (
        <p role="alert" className="notice error">
          {resource.error.message}
        </p>
      )}
      {data && !data.persistence.persistent && (
        <p className="notice pending" role="status">
          History is kept in memory only and is lost when the API restarts. Set
          UQF_FRONTEND_ACTIVITY_DB or UQF_FRONTEND_STACK_ROOT on the API to keep
          it.
        </p>
      )}
      {data && data.persistence.failures > 0 && (
        <p className="notice error" role="alert">
          {data.persistence.failures} history write(s) failed since the API
          started; the actions themselves went ahead unrecorded. Last:{" "}
          {data.persistence.last_error}
        </p>
      )}
      {data && (
        <section className="panel">
          <p className="note">
            Kept, per level:{" "}
            {data.bounds
              .map(
                (b) =>
                  `${b.level} ${b.retention_days} days, newest ${b.max_kept.toLocaleString()}`,
              )
              .join(" · ")}
            . Operational logging, not tamper-proof evidence. Browser rows are
            what the page reported; server rows are the audit. A claimed user is
            whatever the caller sent - it is not verified.
          </p>
          {data.drops.length > 0 && (
            <p className="note" role="status">
              Not in the history, counted:{" "}
              {data.drops
                .map(
                  (d) => `${d.level} ${d.reason} ${d.count.toLocaleString()}`,
                )
                .join(" · ")}
            </p>
          )}
          {data.truncated && (
            <p className="notice pending" role="status">
              Showing the newest {data.events.length.toLocaleString()}; more
              events match these filters.
            </p>
          )}
          {!data.events.length ? (
            <p className="empty">No activity matches these filters.</p>
          ) : (
            <div
              className="table-scroll"
              tabIndex={0}
              aria-label="Activity history"
            >
              <table>
                <thead>
                  <tr>
                    <th>time</th>
                    <th>level</th>
                    <th>source</th>
                    <th>kind</th>
                    <th>action</th>
                    <th>target</th>
                    <th>subject</th>
                    <th>outcome</th>
                    <th>items</th>
                    <th>trigger</th>
                    <th>duration ms</th>
                    <th>status</th>
                    <th>claimed user (unverified)</th>
                    <th>request</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((event) => (
                    <tr key={event.event_id}>
                      <td className="mono">{event.time}</td>
                      <td>{event.level}</td>
                      <td>{event.source}</td>
                      <td>{event.kind}</td>
                      <td>
                        <code>{event.action}</code>
                      </td>
                      <td>{event.target ?? "—"}</td>
                      <td>{event.subject ?? "—"}</td>
                      <td>
                        {event.outcome ? (
                          <span className={`badge ${event.outcome}`}>
                            {event.outcome}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{items(event) || "—"}</td>
                      <td>{event.trigger ?? "—"}</td>
                      <td>{event.duration_ms ?? "—"}</td>
                      <td>{event.status_code ?? "—"}</td>
                      <td>{event.claimed_user ?? "—"}</td>
                      <td>
                        {event.request_id ? (
                          <button
                            type="button"
                            className="chip"
                            data-activity="activity.request"
                            title="Show only this operation"
                            onClick={(e) =>
                              apply(
                                { ...EMPTY, requestId: event.request_id! },
                                triggerOf(e),
                              )
                            }
                          >
                            {event.request_id.slice(0, 8)}
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </>
  );
}
