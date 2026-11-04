# UQF browser application

Local React/TypeScript interface for the existing `uqf_frontend` API.
The browser makes only HTTP requests to the API; gateway credentials stay
on the server. No authentication service or remote hosting is introduced.

## Run locally

Requires Node **`^22.13 || ^24 || >=26`** (CI uses Node 24) and the
repository's uv environment. That is the intersection of what the toolchain
declares, not a round number: `jsdom` sets the 22.x floor at 22.13, and
`vitest` supports no 23.x at all.

`npm ci` warns rather than fails on an unsupported Node, and CI only ever
runs one version — so a dependency quietly raising its floor above the range
above is invisible from here. `src/engines.test.ts` asserts the range we
advertise is one every installed dependency actually accepts, which is the
check npm does not do.
From the repository root, start the API:

```sh
uv run uvicorn --factory uqf_frontend.app:create_app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
npm --prefix web ci
npm --prefix web run dev
```

Open the printed local URL, normally `http://127.0.0.1:5173/ui/`.
Vite proxies only the API paths to `http://127.0.0.1:8000`; set
`UQF_API_ORIGIN` on the Vite process if the API uses another local port.
This is a server-side development proxy, not a credential exposed to React.

## Serve the built app from the API

```sh
npm --prefix web run build
UQF_FRONTEND_STACK_ROOT="$PWD" uv run uvicorn --factory uqf_frontend.app:create_app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/ui/`. API routes and static assets share the same
origin. The stack root's `web/dist` is mounted when it exists at startup; without
it the API runs alone.

## Views

- **Coverage:** explicit dataset, source version and timezone-aware half-open
  range; show composed coverage and every missing interval. No source version
  is guessed. The upstream coverage schema is still unverified (#60), and the
  view states that limitation.
- **Backfills:** state, source version, cursor, row/window counts, errors,
  warnings and update timestamps. `idle` is success with no work; `completed`
  is success with work; `failed` remains a failure. Missing configuration and
  unreadable status files are distinguished from an empty result.
- **Desk:** a glimpse of published data. The catalog's tables are a panel of
  choices; choosing one shows its first 100 rows from the RDB at once, with
  nothing to submit. Tier, row limit, filters and a coverage requirement sit
  behind a collapsed _Refine_ section: tier and limit re-run on change,
  filters apply on a button because they need a typed value. The server
  catalog determines table, filter columns and operators.
  Numeric/boolean values are typed; `in` takes a JSON list. Timestamps require
  an explicit timezone. RDB, HDB and combined results are labelled; HDB latency
  is explained. An optional coverage requirement refuses incomplete queries
  and shows the API's 409 missing-range message. Results are bounded by the
  requested row limit and server cap, with no pagination claim.
  _Plot_ draws the loaded rows instead of tabling them: an x column (numeric
  or temporal), up to three numeric y columns, as a line or scatter. Nulls and
  infinities stay gaps, and a truncated result says so on the chart. No new
  request is made. Plotly loads only when a plot is first shown, from its own
  chunk.
- **Control:** the write surface, rendered only when the API reports writes
  enabled. Processes are picked from the list the API serves, grouped by
  proctype, with `all` meaning torq.sh's own `all` (startwithall=1) rather
  than every name spelled out; processes `all` would not start are marked
  `manual`, and each shows up/down when the Fleet view has a process.csv.
  Then start/stop/restart, a process.csv field override, a live worker-config
  value, or a backfill run.
  Stop and restart ask to be confirmed first; Escape or Cancel backs out.
- **Activity:** the history `GET /activity` serves - what operators did here
  and the server's audit of every write, whoever sent it - filtered by time,
  this session, action, target, outcome, source and level. Choosing a request
  id shows one operation: the click, the request and its result. It says when
  history is in memory only or writes to it have failed, how much of each level
  is kept and what was not, and shows a claimed user as unverified. It sets the
  level this tab records at, and exports what the filters show as a timeline,
  CSV or JSON Lines.
- **Fleet, Queue, Connections, Usage:** the existing operational endpoints,
  including partial failures, identity mismatches, unresolved ports, optional
  processes and unconfigured usage collection.

## Activity recording

`src/activity.ts` records at the tab's level (#2000) - the server's default from
`/health`, or the one picked in the Activity view:

- `off`: nothing. The server still audits every write.
- `actions`: navigation, selections, submissions, confirmations, cancellations
  and the result of each write, from the handler that performs each.
- `interactions`: also every control used, recorded by one listener at the
  document - a click (or Enter or Space on a control), a download, a change to
  a select, box or field, a section toggled, a copy - under the control's
  `data-activity` name. Give every new button, link, input, select, textarea
  and summary one: `src/ActivityCoverage.test.tsx` fails on a control without
  it, in the source and on every rendered view.
- `debug`: also each request's start and finish (method, route, status,
  duration) and each poll and retry of a polled view, rate-limited (a burst of
  30, then 5 a second).

Each event says how it was triggered (`pointer` or `keyboard`: Enter on a button
counts). It batches events and sends them to `POST /activity/events` about a
second later, or when the tab is hidden, and keeps up to 1000 while the API is
unreachable; what the rate limit samples away or the queue overflows is counted
and sent with the next batch. It never records a typed value, a file's name or
contents, copied text or the write token at any level: a target names a view, a
table, a process selector, a worker or a control, a request is recorded by its
route alone, and `detail` carries only the keys the server allows. Every write carries `X-UQF-Request-Id` and `X-UQF-Session-Id`,
so its browser events and the server's audit row share an id.

## Refresh behaviour

Only mounted views poll. Each successful response supplies `poll_seconds`;
requests are scheduled after completion, so slow HDB calls never overlap.
The browser cancels requests on navigation, replacement or refresh, and ignores
late responses. A browser request times out after
`UQF_FRONTEND_TIMEOUT` plus 15 seconds, which `/health` serves as
`request_timeout_seconds` (45 seconds with the default 30 until the first
answer); start/stop/restart are killed 5 seconds sooner, so the server's
refusal arrives before the browser gives up.

Transient errors retry on the last returned cadence, or after 5 seconds before
the first successful response. Permanent errors wait for manual refresh or
changed inputs. Last successful data stays visible during a failure and is
explicitly marked stale. Pause stops automatic refresh; manual refresh still
works. Submitted query/coverage inputs are fixed until the next submission.

Gateway health polls separately. Reloading is a temporary status, never a
worker failure. No q-side events, subscriptions or scheduler facts are invented.

## Checks

```sh
npm --prefix web run check
npm --prefix web run build
```

Tests exercise the rendered interface and request/timer lifecycle in jsdom.
They cover typed filters, UTC range validation, cancellation, no-overlap polling,
server cadence, transient recovery, coverage gaps/409s and idle versus failed
worker states. The CI `Browser application` job runs these and the production
build. API tests cover the added cadence fields and optional static mount.
The build ends with `scripts/check-bundle.mjs`, which reads the vite manifest
and fails if Plotly is in anything the entry loads up front.

Live gateway integration and visual browser QA are separate checks; the unit
suite uses explicit API fixtures and does not silently replace unavailable
live data with demo rows in the application.
