# uqf-frontend

Backend-for-frontend over the uqf TorQ gateway. Implements phases **B0**
through **B3** of
[`docs/reference/frontend-requirements.md`](../../docs/reference/frontend-requirements.md).

## What this is

A thin server-side API layer in front of the TorQ `gateway`, reusing the kola
IPC pattern already proven in this repo by `uqf_client` and by
`torq_orchestrator.core.query()` rather than introducing a second mechanism
(**FE-16**). REST rather than WebSocket, because the gateway path has no
push or subscribe mechanism to a browser client — every view is poll-only
(**FE-10**).

## The property worth knowing

**Client input never reaches q as text.**

The q programs in `queries.py` are constants written in this package. A
caller's table, column names and operator are checked against the whitelist
in `catalog.py` and then passed as IPC *arguments*; a caller's values are
passed as typed IPC arguments and never rendered into query text at all.
That satisfies **FE-14**, and is strictly stronger than escaping or quoting a
concatenated string.

Parameterisation survives the tier boundary too. A TorQ backend evaluates a
routed query with `value` (`gateway.q:253`), and `value` applied to a *list*
**applies** rather than parses — `value ("{[s] ...}"; `EURUSD)` runs the
lambda on the argument. The program travels as the head of that list, so the
backend never parses caller input either.

Three q details this rests on, all verified against a live KDB-X process
rather than assumed:

- A functional select accepts the table *name* as a symbol, so there is no
  `get` on caller-influenced input anywhere in the path.
- In a functional where-clause a bare symbol is read as a **column name**, so
  a symbol *value* must be enlisted or it silently becomes a column
  reference. Any other type must **not** be enlisted, because a 1-element
  list does not broadcast against a column vector and raises `'length`.
- The lambda must be sent as a **char vector**, meaning `bytes` from Python.
  kola maps `str` to a q *symbol*, and a symbol at the head of the query list
  makes `value` try to resolve a variable named the entire lambda text.
- A **niladic** `{[] ...}` sent with no arguments makes q return the *function
  itself*, which kola cannot deserialise. Such a program must be a plain
  expression. This bit twice (`queries.PING`, `ops.IDENTITY`), so
  `test_q_programs.py` now asserts no program is a bare niladic lambda.

### A naming trap worth knowing

**A q builtin cannot be used as a lambda parameter name.** It raises a bare
`'nyi` when the lambda is *called* — not when it is defined, and whether or
not the body references it. `{[ds;sv] 1+1}` fails because `sv` is
scalar-from-vector. This has cost this repository three debugging sessions
(`desc` and `tables` in `scripts/processes/torq_pipeline.q`, `sv` here), so
`test_q_programs.py` now checks every parameter against the 182 reserved and
`.q` names.

## Endpoints

| | |
|---|---|
| `GET /health` | BFF liveness plus gateway reachability. An EOD reload reports `ok: true, gateway: "reloading"` — a known transient state, not a failure (**FE-12**) |
| `GET /catalog` | The queryable surface, so a UI builds filter controls from the server's whitelist instead of a hardcoded copy that drifts |
| `GET /coverage` | Composed coverage intervals and any gaps, for one dataset at one source release (**FE-09**) |
| `GET /ops/queue` | Pending and running gateway queries (**FE-02**) |
| `GET /ops/connections` | Registered backend handles and connected clients (**FE-03**) |
| `GET /ops/usage` | Fleet-wide query log, assembled here because q has none (**FE-04**) |
| `GET /ops/processes` | Fleet health for every process `process.csv` declares (**FE-01**) |
| `GET /ops/backfill` | Backfill and Airflow task status, read from the files q writes (**FE-06**) |
| `POST /query` | Validated, parameterised table query, tier-routed (**FE-07**, **FE-08**) |

### Control routes — these CHANGE things

| | |
|---|---|
| `GET /control` | Whether writes are on, what can be set, and (with writes on) every process a selector may name — `procname`, `proctype`, and whether `all` starts it — from the orchestrator's effective `process.csv`. Not itself gated: a UI needs this to decide whether to render controls, and finding out by provoking a 403 on a lifecycle route means having already stopped the fleet |
| `POST /control/process/{start\|stop\|restart}` | Lifecycle for a process selector — a name, several separated by spaces, or `all`, passed to `torq.sh` unreinterpreted |
| `PUT /control/process/{procname}/config` | One `process.csv` field override, persisted to `process_overrides.csv` and applied on the next start. Returns the effective row |
| `PUT /control/worker-config` | One `.qwcfg` override in the live process the gateway addresses. Returns `.qwcfg.explain`, which names the layer that actually answered |
| `POST /control/backfill` | Launch a bounded worker over a range. **Detached** — watch `/ops/backfill` for the outcome |

**They are off by default, and that is the security posture rather than
caution.** `UQF_FRONTEND_ENABLE_WRITES` must be set on the server or every
one of them returns 403 naming that variable. The reason is FE-15 and FE-20:
this deployment has one shared credential, and the caller's identity is
*claimed* through a header anyone can set. That is defensible while every
route is a read. Once a route can stop the fleet, "anyone who can reach the
port" is the whole access control.

`clean` is deliberately **not** exposed. It deletes logs, tplogs, wdb and the
copied sample data — the one orchestrator verb whose blast radius is data
rather than process state. `uqf-stack clean` remains, where the person
running it is at a terminal on the host.

A backfill is launched detached rather than awaited: it runs for as long as
its range takes, and a request that blocked on one would time out mid-run and
tell the caller nothing about whether the work continued. Every bound must
carry an explicit offset, for the same reason `/coverage` requires one.


Errors carry a `transient` flag so a UI can tell an EOD window or a timeout
apart from a real failure.

```bash
curl -s localhost:8000/query -H 'content-type: application/json' -d '{
  "table": "trades",
  "filters": [
    {"column": "sym", "op": "in", "value": ["EURUSD", "GBPUSD"]},
    {"column": "time", "op": "gt", "value": "2026-09-15T10:30:00Z"}
  ],
  "limit": 500
}'
```

A timestamp **must** carry an explicit timezone offset. Everything is stored
UTC (**ETL-08**, **R9.1**), and kola rejects a naive datetime anyway — so a
browser's local time is refused at the boundary with a reason rather than
being silently wrong.

## Running

```bash
uv run uvicorn --factory uqf_frontend.app:create_app --port 8000
```

Against the local demo stack, set the credential first — the defaults below
leave it empty on purpose (**FE-14**: a credential baked into the package is
how a real one ends up committed beside it), and the gateway's access list
refuses an empty one:

```bash
export UQF_FRONTEND_GATEWAY_USER=admin UQF_FRONTEND_GATEWAY_PASSWD=admin
```

`admin:admin` is what `uqf-stack query` uses against the generated
appconfig. The port needs no setting: it is derived from the base port, and
a stack on another base only needs `UQF_FRONTEND_BASE_PORT`.

Configuration is environment-only, so credentials stay server-side
(**FE-14**):

| Variable | Default |
|---|---|
| `UQF_FRONTEND_GATEWAY_HOST` | `localhost` |
| `UQF_FRONTEND_GATEWAY_PORT` | `6057` — the base port +7, where `process.csv` puts `gateway1`; derived from `UQF_FRONTEND_BASE_PORT` when that is set |
| `UQF_FRONTEND_GATEWAY_USER` / `_PASSWD` | empty — set both; `admin`/`admin` for the demo stack |
| `UQF_FRONTEND_TIMEOUT` | `30` |
| `UQF_FRONTEND_MAX_ROWS` | `10000` |
| `UQF_FRONTEND_PROCESSES` | empty — `rdb1:6052,hdb1:6053` or `name:host:port` |
| `UQF_FRONTEND_PROCESS_CSV` | unset — TorQ's generated `process.csv` |
| `UQF_FRONTEND_BASE_PORT` | `6050` — what `{KDBBASEPORT}` resolves to, and what the gateway port is derived from |
| `UQF_FRONTEND_STATUS_DIR` | unset — where q writes status files (pairs with `UQFSTATUSDIR`) |

A malformed numeric value fails at startup rather than falling back to a
default — the same posture **ETL-14** takes on the q side.

## Tests

```bash
uv run pytest
uv run ruff check .
```

`FakeGateway` records every call, so the security tests assert on *what was
sent*: that the program text is one of this package's own constants, and that
a hostile value appears only in the argument list. No q process required.

`test_catalog_drift.py` parses the schema constants out of
`torq_orchestrator/core.py` as text — no import, so it cannot silently skip
when that package's environment is unavailable.

## Tier routing and coverage (B1)

`POST /query` takes `tier`: `rdb` (today's session), `hdb` (completed
partitions) or `both` (razed). The split is explicit rather than hidden
because **FE-08** requires it and **FE-11** expects `hdb` to be slower — the
response echoes the tier that served it, so a UI can show which it got.

`require_coverage` is an opt-in pre-check that refuses the query with **409**
and *names the missing ranges* when the requested window is not fully
published:

```json
{"table": "trades", "filters": [],
 "require_coverage": {"dataset": "trades", "source_version": "v1",
                      "range_from": "2026-09-13T00:00:00Z",
                      "range_to":   "2026-09-16T00:00:00Z"}}
```

`source_version` is **mandatory**, not optional, because **ETL-09** requires
coverage consumers to filter on it — coverage under one source release says
nothing about another. The filter is applied inside the q program rather than
afterwards in Python, so a caller cannot omit it.

Interval composition follows **ETL-08**: `[range_from, range_to)` is half-open
and adjacent intervals compose **only at their common boundary**, so
`[Mon,Tue)` and `[Tue,Wed)` merge while `[Mon,Tue)` and `[Wed,Thu)` leave
Tuesday as a reported gap. That arithmetic is pure and unit-tested without a
gateway.

### Known gap

`etl_coverage`'s real schema could not be verified from this repo — it exists
only upstream. The `COVERAGE` program assumes `dataset`, `source_version`,
`range_from`, `range_to`, which is what **ETL-08**/**ETL-09** and the frontend
requirements together imply. Confirm before trusting it against a real
ledger.

## Ops views (B2)

Three sources, all already reachable, none needing q-side work. Two are read
from the **gateway process itself** rather than routed to a backend tier —
which still respects the gateway-only boundary, since the gateway is the
thing being asked about.

`/ops/usage` is the interesting one: `.usage.usage` is per-process and **no
fleet-wide rollup exists in q** (FE-04), so it is fanned out and merged here.
The rule that shaped the design: **one unreachable process must not blank the
view.** Unreachable processes are part of the response, not an error —

```json
{"row_count": 9, "processes_configured": 10,
 "unreachable": [{"process": "hdb1", "error": "connection refused"}]}
```

`processes_configured` is there because an empty log with nothing configured
looks identical to an idle fleet. Every view also serves its own
`poll_seconds`, because FE-10 makes polling the only mechanism and the right
interval depends on how fast the underlying state moves.

Configure the fan-out targets with
`UQF_FRONTEND_PROCESSES=rdb1:6052,hdb1:6053` (or `name:host:port`). Empty by
default — so the view says it has nothing configured rather than lying.

### Two live-process findings

- `.gw.servers` is **keyed** by serverid and its `attributes` column holds a
  **dict per server**, which kola cannot serialise at all (`Not supported
  nested list - k type 99`). The program unkeys the table and drops that one
  column; leaving it in fails the entire view for a field no dashboard shows.
- **`.usage.flushtime` is one day, and the "three hours" once recorded here
  was wrong.** `code/handlers/logusage.q` reads `@[value;`flushtime;0D03]`,
  but that is a fallback for a value already set — `config/settings/default.q`
  defines `1D00` first, so the fallback never fires. Measured on three live
  processes: one day. The requirements' "one day" was right all along. Read it
  with `ops.FLUSHTIME`; a deployment may override it again.

## Fleet health (B3)

`GET /ops/processes` reports every process `process.csv` declares, probed for
liveness. The declared set comes from the **generated** `process.csv` — set
`UQF_FRONTEND_PROCESS_CSV`, plus `UQF_FRONTEND_BASE_PORT` so `{KDBBASEPORT}+N`
resolves to the ports the stack actually started on.

### Why this wasn't blocked on FE-22

FE-01 describes liveness as shelling out to `torq.sh` and inspecting **local OS
processes**, and FE-22 asks whether this is for the local demo or a
production-shaped deployment — which makes B3 look gated.

Liveness here comes from an **IPC probe** instead, which dissolves most of
that gate: it doesn't shell out per request, and it works whether or not the
process is on this machine. A process that answers IPC is up in the only
sense a frontend cares about.

What a probe *cannot* distinguish is a process that was never started from
one that started and crashed — both simply don't answer. That needs OS or
supervisor knowledge, and is called out rather than guessed.

### Three states, not two

- **up** — answered, with its self-reported pid, port, procname and proctype.
- **down** — didn't answer. `down_unexpected` excludes `startwithall=0`
  processes, since those being down is configured behaviour rather than a
  fault. `tap1` is exactly that case, and counting it would make the headline
  number permanently wrong.
- **undetermined** — the port placeholder couldn't be resolved, so liveness
  was never testable. Reporting that as "down" would be a guess.

### The failure a port check misses

A process answering the right port may be *the wrong process*. Health
compares self-reported `procname` against what `process.csv` declares:

```
port 6053 is answering as 'WRONG1', but process.csv declares 'hdb1' there
```

Verified live. A stale process squatting a port looks perfectly healthy to
anything that only asks whether something is listening. `unknown` (a plain q
process with no `.proc`) is treated as absence of information, not evidence
of the wrong process.

## Usage capture (FE-13)

`.usage.usage` rows are flushed to disk and dropped from memory after
`flushtime`. Any view of error or latency history longer than that window is
therefore **not a query — it is a capture pipeline**, and it has to run
before the rows are pruned.

This is why B2 builds it rather than deferring it alongside the views that
read it: get it wrong and the history in between is simply gone, which is not
true of most bugs.

```python
capture = UsageCapture(KolaFleet(settings), JsonlSink(Path("captured")))
capture.capture_once()  # safe to call repeatedly, on any scheduler
```

The ordering is deliberately dull and matches **ETL-05**'s
publish-before-checkpoint rule: fetch everything strictly newer than the
watermark, append to the sink, and advance the watermark **only after the
append succeeds**. A failed write keeps the old watermark so the next pass
retries the same rows rather than losing them, and because the fetch filters
strictly greater, a successful pass captures each row exactly once.

## Backfill status (B4)

`GET /ops/backfill` reads the status files q writes, rather than calling
Airflow's REST API. That keeps q authoritative for the facts **ETL-15** says it
owns and adds no Airflow dependency to a frontend that should work without
one. Set `UQF_FRONTEND_STATUS_DIR` to the directory `.qstatus.status_dir`
writes into.

**The format is defined here, not inherited.** This tree has no Airflow
provider to be compatible with, so `.qstatus.write_status` in
`src/etl/core/status.q` defines it and `status.py` consumes it.
`test_status.py` parses the q source to assert the two field sets and state
sets match — without that, adding a field on one side would silently drop
data on the other.

### The boundary this deliberately does not cross

**ETL-15** splits authority: q owns process startup, source reads, query
failures, checkpoints, run and window counts, and coverage events; Airflow
owns task ordering, scheduling, retries, timeouts, concurrency and alerting.
These files carry only the first set, and a test asserts no Airflow-owned
field (`retries`, `try_number`, `timeout`, `concurrency`, `queue`) appears in
the response. Inferring one layer's facts from the other's output is exactly
what ETL-15 forbids.

### Three outcomes, not two

`idle` is a **success**, distinct from `completed`: "ran, found no work" is
not "ran, did work", and neither is a failure. An orchestrator that cannot
tell them apart retries a successful no-op forever. The summary
counts `failed` separately from `running` for the same reason — a worker
still in flight is not a problem.

Writes are atomic (serialise, temp file, rename), because the frontend polls
(**FE-10**) and would otherwise be able to read a half-written file. Files
ending `.tmp` are ignored by the reader, and a test covers that.

## Authorisation seam (B5)

`create_app(policy=...)` takes an authorisation policy, defaulting to
`allow_all`. Every data route passes through it before touching the gateway:
`/query`, `/ops/queue`, `/ops/connections`, `/ops/usage`, `/ops/processes`,
`/ops/backfill`. `/health` and `/catalog` are deliberately outside it —
gating liveness and the surface description would leave an unauthorised
caller unable to discover why.

### Why this is a seam and not an auth system

**FE-20** says the layer connects with one service credential, so q never
sees a per-user identity. **FE-22/FE-23** say local demo, single host, so there
is no user directory and in practice one operator.

Together those make #59's stated acceptance criterion — "two users with
different entitlements get different result sets" — **unreachable**, not
because it is hard but because there are no distinct users to distinguish.
A login flow here would be inventing a requirement.

What is useful now is one place every request passes through, defaulting to
allow, exercised by tests, ready for a real policy the moment an identity
exists. **FE-14** remains what actually carries the security weight:
credentials stay server-side and no client input reaches query text.

```python
from uqf_frontend.authz import deny_tables

app = create_app(policy=deny_tables({"position"}))  # 403 on that table
```

The identity comes from an `x-uqf-user` header and is **claimed, not
verified** — anyone can set it. It is a label for audit and for a policy to
key on, and `authz.py` says so at length so nobody mistakes it for proof.

A refusal is **403, not 401**: there is no authentication to have failed. And
a refused query never reaches q — a test asserts the gateway saw nothing,
since that is what makes this a gate rather than a filter on the way out.

The capture pipeline ships as a callable, not a daemon. What schedules it —
a timer in this process, cron, or an Airflow task — is a deployment question,
and **FE-23** notes no hosting model is established yet.

The React application lives in [`web/`](../../web/README.md), outside the Python
packages. Build it with `npm --prefix web ci && npm --prefix web run build`,
then set `UQF_FRONTEND_WEB_DIST` to the absolute path of `web/dist` when
starting this API. The app is served at `/ui/`; all API paths remain unchanged.

Health, coverage and query responses now also expose `poll_seconds`, using
the same server-owned cadence map as the operations endpoints.
