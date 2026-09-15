# uqf-frontend

Backend-for-frontend over the uqf TorQ gateway. Implements phases **B0**
through **B3** of
[`docs/frontend-requirements.md`](../../docs/frontend-requirements.md).

## What this is

A thin server-side API layer in front of the TorQ `gateway`, reusing the kola
IPC pattern already proven in this repo by `uqf_client` and by
`torq_orchestrator.core.query()` rather than introducing a second mechanism
(**F-16**). REST rather than WebSocket, because the gateway path has no
push or subscribe mechanism to a browser client — every view is poll-only
(**F-10**).

## The property worth knowing

**Client input never reaches q as text.**

The q programs in `queries.py` are constants written in this package. A
caller's table, column names and operator are checked against the whitelist
in `catalog.py` and then passed as IPC *arguments*; a caller's values are
passed as typed IPC arguments and never rendered into query text at all.
That satisfies **F-14**, and is strictly stronger than escaping or quoting a
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
(`desc` and `tables` in `scripts/torq_pipeline.q`, `sv` here), so
`test_q_programs.py` now checks every parameter against the 182 reserved and
`.q` names.

## Endpoints

| | |
|---|---|
| `GET /health` | BFF liveness plus gateway reachability. An EOD reload reports `ok: true, gateway: "reloading"` — a known transient state, not a failure (**F-12**) |
| `GET /catalog` | The queryable surface, so a UI builds filter controls from the server's whitelist instead of a hardcoded copy that drifts |
| `GET /coverage` | Composed coverage intervals and any gaps, for one dataset at one source release (**F-09**) |
| `GET /ops/queue` | Pending and running gateway queries (**F-02**) |
| `GET /ops/connections` | Registered backend handles and connected clients (**F-03**) |
| `GET /ops/usage` | Fleet-wide query log, assembled here because q has none (**F-04**) |
| `GET /ops/processes` | Fleet health for every process `process.csv` declares (**F-01**) |
| `POST /query` | Validated, parameterised table query, tier-routed (**F-07**, **F-08**) |

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
UTC (**E-08**, **R9.1**), and kola rejects a naive datetime anyway — so a
browser's local time is refused at the boundary with a reason rather than
being silently wrong.

## Running

```bash
uv run uvicorn --factory uqf_frontend.app:create_app --port 8000
```

Configuration is environment-only, so credentials stay server-side
(**F-14**):

| Variable | Default |
|---|---|
| `UQF_FRONTEND_GATEWAY_HOST` | `localhost` |
| `UQF_FRONTEND_GATEWAY_PORT` | `6052` |
| `UQF_FRONTEND_GATEWAY_USER` / `_PASSWD` | empty |
| `UQF_FRONTEND_TIMEOUT` | `30` |
| `UQF_FRONTEND_MAX_ROWS` | `10000` |
| `UQF_FRONTEND_PROCESSES` | empty — `rdb1:6052,hdb1:6053` or `name:host:port` |
| `UQF_FRONTEND_PROCESS_CSV` | unset — TorQ's generated `process.csv` |
| `UQF_FRONTEND_BASE_PORT` | `6050` — what `{KDBBASEPORT}` resolves to |

A malformed numeric value fails at startup rather than falling back to a
default — the same posture **E-14** takes on the q side.

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
because **F-08** requires it and **F-11** expects `hdb` to be slower — the
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

`source_version` is **mandatory**, not optional, because **E-09** requires
coverage consumers to filter on it — coverage under one source release says
nothing about another. The filter is applied inside the q program rather than
afterwards in Python, so a caller cannot omit it.

Interval composition follows **E-08**: `[range_from, range_to)` is half-open
and adjacent intervals compose **only at their common boundary**, so
`[Mon,Tue)` and `[Tue,Wed)` merge while `[Mon,Tue)` and `[Wed,Thu)` leave
Tuesday as a reported gap. That arithmetic is pure and unit-tested without a
gateway.

### Known gap

`etl_coverage`'s real schema could not be verified from this repo — it exists
only upstream. The `COVERAGE` program assumes `dataset`, `source_version`,
`range_from`, `range_to`, which is what **E-08**/**E-09** and the frontend
requirements together imply. Confirm before trusting it against a real
ledger.

## Ops views (B2)

Three sources, all already reachable, none needing q-side work. Two are read
from the **gateway process itself** rather than routed to a backend tier —
which still respects the gateway-only boundary, since the gateway is the
thing being asked about.

`/ops/usage` is the interesting one: `.usage.usage` is per-process and **no
fleet-wide rollup exists in q** (F-04), so it is fanned out and merged here.
The rule that shaped the design: **one unreachable process must not blank the
view.** Unreachable processes are part of the response, not an error —

```json
{"row_count": 9, "processes_configured": 10,
 "unreachable": [{"process": "hdb1", "error": "connection refused"}]}
```

`processes_configured` is there because an empty log with nothing configured
looks identical to an idle fleet. Every view also serves its own
`poll_seconds`, because F-10 makes polling the only mechanism and the right
interval depends on how fast the underlying state moves.

Configure the fan-out targets with
`UQF_FRONTEND_PROCESSES=rdb1:6052,hdb1:6053` (or `name:host:port`). Empty by
default — so the view says it has nothing configured rather than lying.

### Two live-process findings

- `.gw.servers` is **keyed** by serverid and its `attributes` column holds a
  **dict per server**, which kola cannot serialise at all (`Not supported
  nested list - k type 99`). The program unkeys the table and drops that one
  column; leaving it in fails the entire view for a field no dashboard shows.
- **`.usage.flushtime` defaults to `0D03` — three hours, not the one day the
  requirements state.** Measured on a live process. A capture pipeline sized
  for a day would lose most of the log.

## Fleet health (B3)

`GET /ops/processes` reports every process `process.csv` declares, probed for
liveness. The declared set comes from the **generated** `process.csv` — set
`UQF_FRONTEND_PROCESS_CSV`, plus `UQF_FRONTEND_BASE_PORT` so `{KDBBASEPORT}+N`
resolves to the ports the stack actually started on.

### Why this wasn't blocked on F-22

F-01 describes liveness as shelling out to `torq.sh` and inspecting **local OS
processes**, and F-22 asks whether this is for the local demo or a
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

## Usage capture (F-13)

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

The ordering is deliberately dull and matches **E-05**'s
publish-before-checkpoint rule: fetch everything strictly newer than the
watermark, append to the sink, and advance the watermark **only after the
append succeeds**. A failed write keeps the old watermark so the next pass
retries the same rows rather than losing them, and because the fetch filters
strictly greater, a successful pass captures each row exactly once.

## Not in scope here

Phases **B3** (process fleet health) and **B4** (Airflow/backfill status) are
the two requirements that need new q-side or Airflow-side work, and both are
gated on open questions (**F-21**, **F-22**). **B5** (auth) is gated on
**F-20**; until then the layer connects with one credential from its
environment.

The capture pipeline ships as a callable, not a daemon. What schedules it —
a timer in this process, cron, or an Airflow task — is a deployment question,
and **F-23** notes no hosting model is established yet.

The React application is deliberately not here. This package is Python; a
browser app should live outside `python/`.
