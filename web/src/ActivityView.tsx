import { useState, type FormEvent } from "react";
import {
  type ActivityEvent,
  type ActivityHistory,
  type ActivityOutcome,
  type ActivitySource,
  validateTime,
} from "./api";
import { record, sessionId, triggerOf } from "./activity";
import { useResource } from "./useResource";

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
  outcome: ActivityOutcome | "";
  source: ActivitySource | "";
  requestId: string;
}

const EMPTY: Filters = {
  since: "",
  until: "",
  thisSession: false,
  action: "",
  target: "",
  outcome: "",
  source: "",
  requestId: "",
};

/** The query string for a set of filters: an empty field filters nothing. */
export function activityQuery(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  if (filters.thisSession) params.set("session_id", sessionId());
  if (filters.action) params.set("action", filters.action);
  if (filters.target) params.set("target", filters.target);
  if (filters.outcome) params.set("outcome", filters.outcome);
  if (filters.source) params.set("source", filters.source);
  if (filters.requestId) params.set("request_id", filters.requestId);
  const text = params.toString();
  return text ? `/activity?${text}` : "/activity";
}

function items(event: ActivityEvent): string {
  return event.items
    .map((item) => `${item.target}: ${item.outcome}`)
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
  const resource = useResource<ActivityHistory>(activityQuery(applied));

  function apply(next: Filters, trigger = triggerOf()) {
    setDraft(next);
    setApplied(next);
    // The filter's shape, not its text: a target someone typed may be anything.
    record("submit", "activity.filter", {
      trigger,
      target: "Activity",
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
      <form className="panel" onSubmit={submit} aria-label="Filter activity">
        <div className="fields">
          <label>
            From (UTC, timezone required)
            <input
              value={draft.since}
              placeholder="e.g. 2026-11-03T00:00:00Z"
              onChange={(e) => setDraft({ ...draft, since: e.target.value })}
            />
          </label>
          <label>
            To (exclusive)
            <input
              value={draft.until}
              onChange={(e) => setDraft({ ...draft, until: e.target.value })}
            />
          </label>
          <label>
            Action
            <input
              value={draft.action}
              placeholder="e.g. process or process.stop"
              onChange={(e) => setDraft({ ...draft, action: e.target.value })}
            />
          </label>
          <label>
            Target
            <input
              value={draft.target}
              onChange={(e) => setDraft({ ...draft, target: e.target.value })}
            />
          </label>
          <label>
            Outcome
            <select
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
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
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
              onClick={(e) => apply(EMPTY, triggerOf(e))}
              disabled={applied === EMPTY}
            >
              Clear
            </button>{" "}
            <button className="primary">Apply filters</button>
          </span>
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
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
            Kept {data.retention_days} days, newest{" "}
            {data.max_rows.toLocaleString()} events. Operational logging, not
            tamper-proof evidence. Browser rows are what the page reported;
            server rows are the audit. A claimed user is whatever the caller
            sent - it is not verified.
          </p>
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
                    <th>source</th>
                    <th>kind</th>
                    <th>action</th>
                    <th>target</th>
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
                      <td className="mono">{event.ts}</td>
                      <td>{event.source}</td>
                      <td>{event.kind}</td>
                      <td>
                        <code>{event.action}</code>
                      </td>
                      <td>{event.target ?? "—"}</td>
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
