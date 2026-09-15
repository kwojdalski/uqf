# uqf-frontend

Backend-for-frontend over the uqf TorQ gateway. Implements phases **B0** and
**B1** of [`docs/frontend-requirements.md`](../../docs/frontend-requirements.md).

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

## Not in scope here

Phases **B3** (process fleet health) and **B4** (Airflow/backfill status) are
the two requirements that need new q-side or Airflow-side work, and both are
gated on open questions (**F-21**, **F-22**). **B5** (auth) is gated on
**F-20**; until then the layer connects with one credential from its
environment.

The React application is deliberately not here. This package is Python; a
browser app should live outside `python/`.
