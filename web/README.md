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
UQF_FRONTEND_WEB_DIST="$PWD/web/dist" uv run uvicorn --factory uqf_frontend.app:create_app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/ui/`. API routes and static assets share the same
origin. The optional static mount fails at startup if the configured directory
does not exist. The API continues to run without the web directory configured.

## Views

- **Coverage:** explicit dataset, source version and timezone-aware half-open
  range; show composed coverage and every missing interval. No source version
  is guessed. The upstream coverage schema is still unverified (#60), and the
  view states that limitation.
- **Backfills:** state, source version, cursor, row/window counts, errors,
  warnings and update timestamps. `idle` is success with no work; `completed`
  is success with work; `failed` remains a failure. Missing configuration and
  unreadable status files are distinguished from an empty result.
- **Desk:** server catalog determines table, filter columns and operators.
  Numeric/boolean values are typed; `in` takes a JSON list. Timestamps require
  an explicit timezone. RDB, HDB and combined results are labelled; HDB latency
  is explained. An optional coverage requirement refuses incomplete queries
  and shows the API's 409 missing-range message. Results are bounded by the
  requested row limit and server cap, with no pagination claim.
- **Control:** the write surface, rendered only when the API reports writes
  enabled. Processes are picked from the list the API serves, grouped by
  proctype, with `all` meaning torq.sh's own `all` (startwithall=1) rather
  than every name spelled out; processes `all` would not start are marked
  `manual`, and each shows up/down when the Fleet view has a process.csv.
  Then start/stop/restart, a process.csv field override, a live worker-config
  value, or a backfill run.
- **Fleet, Queue, Connections, Usage:** the existing operational endpoints,
  including partial failures, identity mismatches, unresolved ports, optional
  processes and unconfigured usage collection.

## Refresh behaviour

Only mounted views poll. Each successful response supplies `poll_seconds`;
requests are scheduled after completion, so slow HDB calls never overlap.
The browser cancels requests on navigation, replacement or refresh, and ignores
late responses. A browser request times out after 45 seconds (the API's default
gateway timeout is 30 seconds).

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

Live gateway integration and visual browser QA are separate checks; the unit
suite uses explicit API fixtures and does not silently replace unavailable
live data with demo rows in the application.
