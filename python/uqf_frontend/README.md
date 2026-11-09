# uqf-frontend

Backend-for-frontend over the uqf TorQ gateway.

## What this is

A thin server-side API layer in front of the TorQ `gateway`, reusing the kola
IPC pattern already proven in this repo by `uqs.stack.runtime.query()` rather
than introducing a second mechanism. REST rather than WebSocket, because the
gateway path has no push or subscribe mechanism to a browser client --- every
view is poll-only.

## The property worth knowing

**Client input never reaches q as text.**

The q programs in `queries.py` are constants written in this package. A caller's
table, column names and operator are checked against the whitelist in
`catalog.py` and then passed as IPC *arguments*; a caller's values are passed as
typed IPC arguments and never rendered into query text at all. That is the
security guarantee, and it is strictly stronger than escaping or quoting a
concatenated string.

Parameterisation survives the tier boundary too. A TorQ backend evaluates a
routed query with `value` (`gateway.q:253`), and `value` applied to a *list*
**applies** rather than parses --- `value ("{[s] ...}"; `EURUSD)\` runs the
lambda on the argument. The program travels as the head of that list, so the
backend never parses caller input either.

The q details this rests on, verified against a live KDB-X process:

- A functional select accepts the table *name* as a symbol, so there is no `get`
  on caller-influenced input anywhere in the path.
- In a functional where-clause a bare symbol is read as a **column name**, so a
  symbol *value* must be enlisted or it silently becomes a column reference. Any
  other type must **not** be enlisted, because a 1-element list does not
  broadcast against a column vector and raises `'length`.
- The lambda must be sent as a **char vector**, meaning `bytes` from Python.
  kola maps `str` to a q *symbol*, and a symbol at the head of the query list
  makes `value` try to resolve a variable named the entire lambda text.
- A **niladic** `{[] ...}` sent with no arguments makes q return the *function
  itself*, which kola cannot deserialise. Such a program must be a plain
  expression; `test_q_programs.py` asserts none is a bare niladic lambda, and
  that no lambda parameter is a q builtin.

## Endpoints

  |                        |                                                                                                                                           |
  | ---                    | ---                                                                                                                                       |
  | `GET /health`          | BFF liveness plus gateway reachability. An EOD reload reports `ok: true, gateway: "reloading"` — a known transient state, not a failure   |
  | `GET /catalog`         | The queryable surface, so a UI builds filter controls from the server's whitelist instead of a hardcoded copy that drifts                 |
  | `GET /coverage`        | Composed coverage intervals and any gaps, for one dataset at one source release                                                           |
  | `GET /ops/queue`       | Pending and running gateway queries                                                                                                       |
  | `GET /ops/connections` | Registered backend handles and connected clients                                                                                          |
  | `GET /ops/usage`       | Fleet-wide query log, assembled here because q has none                                                                                   |
  | `GET /ops/processes`   | Fleet health for every process `process.csv` declares                                                                                     |
  | `GET /ops/backfill`    | Backfill and Airflow task status, read from the files q writes                                                                            |
  | `GET /services`        | The deployment targets `uqs target list --json` lists, read from `deploy_targets.toml` with no ssh, and whether remote operations are on  |
  | `POST /query`          | Validated, parameterised table query, tier-routed                                                                                         |
  | `GET /charts`          | The prepared charts, each with its parameters' JSON Schema and its point limit                                                            |
  | `POST /charts/{name}`  | One prepared chart's plotly.py figure, with its point limit and whether it was downsampled or truncated                                   |
  | `POST /compare`        | Tables on two endpoints (this stack, `local`, or a registered target) over an explicit window: `uqs data diff --json`, delegated whole    |
  | `GET /me/capabilities` | Who the caller is (as a trusted proxy vouches), their groups, and every feature and action the access policy grants them                  |
  | `GET /workspaces`      | Every workspace a request may name: `local` and each target the uqs registry declares, with its connection and access mode - no ssh       |
  | `GET /workspace`       | The workspace the request names, reached now: for a target, the connection uqs holds open and each process's forward                      |

### Control routes --- these CHANGE things

  |                                                  |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
  | ---                                              | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | `GET /control`                                   | Whether writes are on, what can be set, and (with writes on) every process a selector may name — `process`, `proctype`, and whether `all` starts it — from the orchestrator's effective `process.csv`. Not itself gated: a UI needs this to decide whether to render controls, and finding out by provoking a 403 on a lifecycle route means having already stopped the fleet                                                                                                                                |
  | `POST /control/lifecycle/{start\|stop\|restart}` | Lifecycle for a process selector — a name, several separated by spaces, or `all`, passed to `torq.sh` unreinterpreted                                                                                                                                                                                                                                                                                                                                                                                        |
  | `PUT /control/processes/{process}/config`        | One `process.csv` field override, persisted to `process_overrides.csv` and applied on the next start. Returns the effective row. `qcmd` and `host` are refused: they choose what program runs and where, so only `uqs config set` on the host sets them                                                                                                                                                                                                                                                      |
  | `PUT /control/worker/config`                     | One `.qetl.cfg` override in the live process the gateway addresses, lost when it restarts. Returns `.qetl.cfg.explain`, and `shadowed` with the `UQF_<KEY>` variable when the environment outranks the override                                                                                                                                                                                                                                                                                              |
  | `POST /control/backfill`                         | Launch a bounded worker over a range. **Detached** — watch `/ops/backfill` for the outcome                                                                                                                                                                                                                                                                                                                                                                                                                   |

Every request body is held to its model: a key the model does not know is a 422,
never ignored, and the lifecycle body's `procs` is required. It once defaulted
to `all`, so a body that lost the field stopped the whole fleet (#1361).

**What the UI confirms, the API requires confirmed** (#2244). A stop or a
restart, opening a config push, a restore and a file delete each need
`"confirmed": true` in the body; anything else - missing, `false`, `1`, `"true"` -
is a 422, "explicit confirmation is required - send confirmed: true", and
nothing runs. A start takes none. The one check is `models.ConfirmedRequest`, so
a script or a second client gets the guard the browser's dialog stands for.

An unknown name in the path is 404, with an error naming the kind:
`TargetNotFound`, `ChartNotFound`, `ActionNotFound`, `ProcessNotFound`. 422 is
for a body or query that is refused, and 400 only for `QueryRejected`, where q
failed on input that passed validation (#2224, #2225). `errors.py` tables the
status of every error the API raises, and `test_error_shape.py` holds the code
to that table.

Every route that takes a `limit`, or applies a row cap, answers `truncated`:
more rows existed than were returned, whatever cut them (#2220). Each reads one
row past what it returns to know. `/query` also says which bound cut it in
`cut_by`: `limit`, the caller's own, or `max_rows`, a server cap
(`UQF_FRONTEND_MAX_ROWS` or the query policy's maxrows). A read that stops at
the policy's cap cannot see past it, so reaching that cap counts as cut.

**They are off by default, and that is the security posture rather than
caution.** `UQF_FRONTEND_ENABLE_WRITES` must be set on the server or every one
of them returns 403 naming that variable. The reason: this deployment has one
shared credential, and the caller's identity is *claimed* through a header
anyone can set. That is defensible while every route is a read. Once a route can
stop the fleet, "anyone who can reach the port" is the whole access control.

**With writes on, two more checks apply** (`write_guard.py`):

- **A token.** Every state-changing control request must carry
  `Authorization: Bearer $UQF_FRONTEND_WRITE_TOKEN`; without it the answer is
  401. The server refuses to start with writes on and no token. `GET /control`
  stays open, since the UI asks it whether to draw controls at all. The check,
  like the authorisation policy and the audit, judges the path the router
  routes: behind a proxy that serves the API under a prefix
  (`--root-path /api`), `/api/control/...` needs the token as `/control/...`
  does (#2018).
- **An allowed Host.** Every request's `Host` must be in
  `UQF_FRONTEND_ALLOWED_HOSTS` (default `localhost,127.0.0.1,::1`), or it is
  refused with 403. Binding to 127.0.0.1 does not stop DNS rebinding; this does.

```sh
export UQF_FRONTEND_ENABLE_WRITES=true
export UQF_FRONTEND_WRITE_TOKEN=$(openssl rand -hex 32)
```

Under `npm run dev`, the Vite proxy adds the header from the same variable on
the server side, so the browser never holds the token. The built app served at
`/ui/` has no proxy in front of it, so the Control view asks for the token once
and keeps it in the tab's `sessionStorage`.

Removing output is deliberately **not** exposed. It deletes logs, tplogs, wdb
and the copied sample data --- the one orchestrator verb whose blast radius is
data rather than process state. `uqs clean` remains, where the person running it
is at a terminal on the host.

### Browsing a target's files --- reads, but over ssh

`GET /services/{target}/files?path=&limit=` lists one directory under a
registered target's deployment root (#1950). It changes nothing, but it reaches
a server with this machine's ssh key, so it needs what a control action needs:
writes on, the token, an allowed Host. Only a target `deploy_targets.toml`
declares can be named; its ssh destination and root are the registry's, never
the request's.

The listing runs `remote_helper.py` - standard library only, sent on ssh's stdin
every time - with the release's own `<dest>/current/.venv/bin/python -I -`. A
release without that interpreter is refused (409) with nothing else tried and
nothing installed. The path travels base64-encoded, so it never reaches a shell.

`path` is relative to the root: an absolute path, `..`, a NUL byte or a path
through a symbolic link is refused (422) before ssh and again on the server,
which walks one directory descriptor at a time with `O_NOFOLLOW`. Links -
`current` included - FIFOs, sockets, devices and names that are not UTF-8 are
listed as blocked and cannot be entered; the release `current` names, and the
fields of its `deploy-report.json`, come back as `release`. A listing returns at
most `limit` entries (500 by default, 5000 at most) and says when it was
truncated; the call is held to 20 s.

`DELETE /services/{target}/files`, with the body
`{"paths": [...], "confirmed": true}`, removes named regular files and empty
directories, never following a link or recursing (#1953). A remote file is one
resource whose method is the operation (#2248): `GET` lists, `PUT` writes and
`DELETE` removes. The old `POST .../files/delete` and `PUT .../files/content`
answer 410 `Gone` naming the route that replaced them. A deletion changes the
server, so once ssh has started, a timeout, ssh exiting 255 (it may have dropped
midway), a crashed helper or an answer that cannot be read is 504
`DeleteOutcomeUncertain` with `transient: false`: some paths may be gone, and
you list the directory before deleting again. Only a browse's timeout is
transient (#2023).

### A target's tunnel --- uqs plans and checks it

`GET /services/{target}/tunnel?process=&local=PROC:PORT&offset=` returns the
target's tunnel plan, and `GET /services/{target}/tunnel/check` with the same
parameters returns the result of checking it (#2030, #2031). Both are gated like
browsing, because uqs reads the target's release over ssh. Neither route has ssh
code of its own: `tunnels.py` calls `uqs.deploy.tunnel_plan` and
`uqs.deploy.tunnel_check`, the functions behind `uqs target tunnel plan` and
`uqs target tunnel check`.

Every value the routes return comes from uqs: the processes and their ports,
each local endpoint, the launch command (which holds no credential), each
mapping's `verified`, `stale` or `unverified` state, and each endpoint's
diagnostic category. A refused plan returns 422, and a release that cannot be
asked for its processes returns 502.

The check logs in to q with this API's gateway credential when one is
configured, and with uqs's default otherwise. The credential never appears in a
response. The Services view shows the plan and the check. The Fleet view offers
the same connection details for whichever target you choose.

### Uploading to a target's shared/config --- one file per request

`PUT /services/{target}/files?path=&overwrite=false&push_id=` writes the request
body to `path` below the target's `shared/config/` - the operator-supplied files
every release links in - and nowhere else (#1951). It is gated like browsing:
writes on, the token, an allowed Host, a registered target. The browser's file
browser shows an upload queue in `shared/config/` and below it. You can choose
files or a folder, or drag them in. Nested paths are kept. Nothing is sent until
you confirm the destination. Each file has its own progress and its own outcome.

- **Limits.** 16 MiB a file, checked in the browser, again by the API before ssh
  (`Content-Length`, then while the body is read: 413), and again on the server.
  The browser also caps a queue at 500 files and 256 MiB. A path must be
  relative, without `..`, NUL or a name over 255 bytes, at most 16 directories
  below `shared/config/`. Directories that do not exist are created. A link on
  the way is refused, never followed.
- **Framing.** `list` runs the helper as `python -I -`, which reads stdin to its
  end as source. So an upload runs `python -I -c BOOTSTRAP <request>`, and stdin
  carries `<len(source)>\n`, the source, then exactly `size` raw bytes.
  `BOOTSTRAP` is fixed, single-line text. The base64 request carries the path,
  `size`, `sha256` and `overwrite`, so no path reaches a shell.
- **Atomicity.** The bytes go to `.uqf-upload-<random>.tmp` in the destination
  directory, created with `O_EXCL|O_NOFOLLOW`. They are counted and hashed, then
  fsync'd. A wrong size or sha256 publishes nothing. Without `overwrite`, the
  file is published with `link(2)`, which fails if the name exists, with no
  window between check and write. With it, the file is published with
  `rename(2)`. A reader sees the old file or the whole new one. A link,
  directory or special file at the name is never replaced. A replaced file keeps
  its permission bits (not setuid, setgid or sticky). A new file gets 0666 less
  the umask.
- **Cleanup.** The temp file is removed on every failure. A hangup or SIGTERM
  from a dropped or timed-out ssh while the bytes arrive becomes a refusal so
  that the cleanup runs. From the publishing step on, both are ignored: the file
  may be live by then, so the upload finishes and says so rather than report
  that nothing was published (#2022). Only SIGKILL leaves a temp file behind,
  and the next upload to that directory sweeps any older than an hour.
- **Uncertain outcomes.** An existing file without `overwrite` is 409. A refused
  path is 422, and a push the upload names that does not exist, or a deployment
  root that is not there, is 404 `PathNotFound`. Damaged bytes are 502
  `UploadIncomplete`. In each of these, nothing was written. A timeout (120 s),
  an ssh failure (255), a helper crash or a lost answer is 504
  `UploadOutcomeUncertain`, with `transient: false`. The file may or may not be
  there, and neither the API nor the browser retries: the queue shows "Outcome
  unknown", and you list the directory before uploading it again. Cancelling an
  upload in flight is reported the same way.
- **Audit.** Each upload is one `files.upload` row in the activity history
  (#1954). The row records the target, the file's path, size, sha256, and
  whether it was a create or an overwrite. It never records the file's bytes. An
  uncertain upload is recorded as `unknown`, a refusal as `refused`, and damaged
  bytes as `failed`.

### Config pushes: one push id, history, diff and restore

Every upload belongs to a push (#2002): a file selection, a folder or a drop,
opened with `POST /services/{target}/config/pushes` when you confirm it, so the
open carries `"confirmed": true`. Each upload names it (`push=`), and
`POST .../config/pushes/{id}/close` closes it with what the browser saw of each
file. The server keeps, and only the server keeps, what the push changed:

```text
<dest>/shared/config/.uqf-history/<push-id>/      0o700, never browsable
    manifest.json     who, when, where; per file its action, sizes,
                      sha256s, modes and state (pending/published/failed)
    before/<path>     the file a push replaced (or a restore deleted)
    after/<path>      the file as the push wrote it
```

A push id is `YYYYMMDDTHHMMSSmmmZ-<8 hex>`, UTC, so ids sort by time. The
history is walked with the same confined, no-follow descriptor steps, and no
browse, delete or upload path may name `.uqf-history`, in any letter case.

- **Reads.** `GET .../config/pushes` lists a target's pushes, newest first.
  `GET .../config/pushes/{id}` shows each file's change, its old and new sha256,
  and what it is now: as the push left it, as before it (an upload that never
  landed), or changed since. `GET .../config/pushes/{id}/diff?path=` diffs
  before against after as text under 256 KiB; a binary or larger file shows
  sizes and hashes only. These reads are not audited, so a diff's text never
  reaches the activity store.
- **Restore.** `GET .../config/pushes/{id}/restore` is the plan; `POST` with
  `{"confirmed": true}` writes each replaced file's earlier content back and
  deletes each file the push created (a regular file, never a link), as a push
  of its own - so a restore can be restored. A file changed since the push
  refuses the whole restore (409 `RestoreConflict`), writing nothing. A lost
  answer is 504 `RestoreOutcomeUncertain`, never retried.
- **Retention.** Opening a push prunes the target's history to the newest
  `UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT` pushes (50), none older than
  `UQF_FRONTEND_CONFIG_HISTORY_RETENTION_DAYS` (90) - or the values the Settings
  view kept in `UQF_FRONTEND_SETTINGS_FILE`, which the API and
  `uqs target config restore` both read over those. Each pruned id is logged in
  `.uqf-history/pruned.jsonl`, so its restore is refused as pruned (410
  `HistoryPruned`), not as unknown.
- **Audit.** `config.push.open` when a push opens; `files.upload` per file, with
  `push_id` and `previous_sha256` (null on a create); `config.push` when it
  closes, with the destination, file count, bytes and one outcome per file;
  `config.restore` for a restore, with `restores` and its own `push_id`;
  `config.history.prune`, one item per pruned push. File bytes are never in a
  row or an export.
- **CLI.** `uqs target config history TARGET`, and `show`, `diff` and `restore`
  with a push id, read the same history over ssh with the same helper; `restore`
  takes `--dry-run` and asks first unless given `--yes`.

A backfill is launched detached rather than awaited: it runs for as long as its
range takes, and a request that blocked on one would time out mid-run and tell
the caller nothing about whether the work continued. Every bound must carry an
explicit offset, for the same reason `/coverage` requires one.

Every one of them is audited, whoever calls it --- see [Activity history and the
action audit](#activity-history-and-the-action-audit).

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

A timestamp **must** carry an explicit timezone offset. Everything is stored UTC
and kola rejects a naive datetime anyway --- so a browser's local time is
refused at the boundary with a reason rather than being silently wrong.

## Workspaces: which stack a request reads

A browser tab picks a workspace - `local`, this API's own stack, or a target the
uqs registry declares (`GET /workspaces`) - and names it on every request in the
`X-UQF-Target` header. Nothing about the choice is kept on the server, so two
sessions on different targets never move each other (`workspaces.py`, #2029).

- **Scoped routes** read the named workspace: `/query`, `/catalog`, `/coverage`,
  `/charts/{name}`, `/ops/processes`, `/ops/connections`, `/ops/clients`,
  `/ops/queue`, `/ops/usage` and `/workspace`. Each answer, and each error from
  one, carries `X-UQF-Target` naming its workspace.
- **Neutral routes** read no stack and ignore the header: `/health`,
  `/workspaces`, `/charts`, `/me/capabilities`, `/services*` (which name their
  target in the path) and `/activity*`.
- **Every other route** refuses a target with 409 rather than answering for the
  local stack: control, `/ops/backfill`, and any route added later.

A target is reached through `uqs.deploy.workspace`: its release's
`uqs process list`, then one ssh with a loopback forward per declared process,
held open between requests (closed after ten idle minutes, reopened when its ssh
has gone). The q login is this API's own. An unregistered target is 404, one
that cannot be reached is 502 naming it and what failed, and a target needs
remote operations (`UQF_FRONTEND_ENABLE_WRITES`) and `services:read` - none of
these ever falls back to the local stack. A target workspace is read-only:
control acts on this API's own stack only.

Because the ssh opens with the operator's key, a scoped route naming a target
needs what `/services/{target}` needs: the write token and an allowed Host, as
well as writes on (#2067). Without the token it is refused with 401
`WriteTokenRequired` before any ssh opens. The local workspace needs no token,
and neither do the neutral and 409 routes in a target workspace, since they open
no ssh: a tab left on a target can still list the workspaces and switch back.
The web UI sends the token it holds with every request naming a target.

## Running

```bash
uv run uvicorn --factory uqf_frontend.app:create_app --port 8000
```

Against the local demo stack, set the credential first --- the defaults below
leave it empty on purpose (a credential baked into the package is how a real one
ends up committed beside it), and the gateway's access list refuses an empty
one:

```bash
export UQF_FRONTEND_GATEWAY_USER=admin UQF_FRONTEND_GATEWAY_PASSWORD=admin
export UQF_FRONTEND_DATA_USER=browser UQF_FRONTEND_DATA_PASSWORD=browser
```

`admin:admin` is what `uqs query` uses against the generated appconfig. The
second login is for reading tables: `/query` reads as the `browser` role,
through the gateway's `.uqf.browse`, so the gateway's query policy holds every
read ([query policies](../../docs/architecture/query-policies.md)).

Point the frontend at the stack, and it reads the rest from the stack's own
files (`stack.py`): the base port from the `setenv.sh` bootstrap writes, the
gateway's host and port and the fan-out list from the generated `process.csv`,
the backfill status directory as the workers resolve it, and `web/dist` once
built. Nothing restates them, so nothing can disagree:

```bash
export UQF_FRONTEND_STACK_ROOT="$PWD"
```

Configuration is environment-only, so credentials stay server-side:

  | Variable                                  | Default                                                                                                                         |
  | ---                                       | ---                                                                                                                             |
  | `UQF_FRONTEND_STACK_ROOT`                 | unset — the gateway at `localhost:6057`; fleet health, the query log, backfill status and `/ui/` report themselves unconfigured |
  | `UQF_FRONTEND_GATEWAY_USER` / `_PASSWORD` | empty — set both; `admin`/`admin` for the demo stack                                                                            |
  | `UQF_FRONTEND_DATA_USER` / `_PASSWORD`    | empty — set both; `browser`/`browser` for the demo stack. Without it `/query` refuses with 503, naming these                    |
  | `UQF_FRONTEND_TIMEOUT_SECONDS`            | `30`                                                                                                                            |
  | `UQF_FRONTEND_MAX_ROWS`                   | `10000`                                                                                                                         |

A malformed numeric value, or a stack root that is not the repository, fails at
startup rather than falling back to a default.

The React application lives in [`web/`](../../web/README.md). Build it with
`npm --prefix web ci && npm --prefix web run build`; the stack root's `web/dist`
is then served at `/ui/` (read at startup).

## Tests

```bash
uv run pytest
uv run ruff check .
```

`FakeGateway` records every call, so the security tests assert on *what was
sent*: that the program text is one of this package's own constants, and that a
hostile value appears only in the argument list. No q process required.

`FakeGateway` also answers the catalog's questions (`FAKE_CATALOG`,
`FAKE_SCHEMA` in `gateway.py`), so no test here needs a stack;
`tests/q/test_catalog.q` holds the real catalog honest.

## Tier routing and coverage

`POST /query` takes `tier`: `rdb` (today's session), `hdb` (completed
partitions) or `both` (razed). The split is explicit rather than hidden because
`hdb` is expected to be slower --- the response echoes the tier that served it,
so a UI can show which it got.

`require_coverage` is an opt-in pre-check that refuses the query with **409**
and *names the missing ranges* when the requested window is not fully published:

```json
{"table": "trades", "filters": [],
 "require_coverage": {"dataset": "trades", "slice": "", "source_version": "v1",
                      "range_from": "2026-09-13T00:00:00Z",
                      "range_to":   "2026-09-16T00:00:00Z"}}
```

Published is not the same as queryable (#1094). A backfill records a window as
covered as soon as its rows are appended, while the HDB only sees a date once
the backfill has finished it and every running HDB has reloaded. So
`require_coverage` checks twice: first that the range is covered, then that it
is covered *at or before the dataset's readiness watermark*
(`.qetl.coverage.ready_claims`). A range that is written but not yet loaded is
also refused with **409**, with a message that says so and names the window:
retry once the reload has gone through. `/coverage` itself still reports what is
written.

`source_version` is **mandatory**, not optional, because coverage consumers must
filter on it --- coverage under one source release says nothing about another.
The filter is applied inside the q program rather than afterwards in Python, so
a caller cannot omit it.

`slice` is mandatory for the same reason, one dimension further out (#185): the
slice of the dataset the coverage is recorded under, such as a symbol a worker
fills, with `""` for a dataset with no slice dimension. It is `slice` on
`GET /coverage`, `require_coverage` and `/compare`, as `--slice` is on
`uqs data diff` (#2239); "partition" in this API means only a date partition of
the HDB, and a request still sending `partition` is a 422 naming `slice`
(#2261). The ledger itself still calls the column `partition`, as a worker's
declaration does: the API maps `slice` onto it at the q boundary rather than
rewriting the ledger file.

A requested range on `GET /coverage` is both bounds or neither, as everywhere a
coverage window is named: `range_from` alone, or `range_to` alone, is a 422
naming the missing bound (#2222), never a 200 that ignored it.

Interval composition: `[range_from, range_to)` is half-open and adjacent
intervals compose **only at their common boundary**, so `[Mon,Tue)` and
`[Tue,Wed)` merge while `[Mon,Tue)` and `[Wed,Thu)` leave Tuesday as a reported
gap. None of it is done here, and none of it reads a tier: the coverage ledger
is a file the bounded workers persist (`.qetl.coverage.ledger_path`, beside
their checkpoints), held by no RDB or HDB. The gateway loads its reader
([`src/etl/core/materialisation.q`](../../src/etl/core/materialisation.q)), so
`/coverage` and `require_coverage` call `.qetl.coverage.claims` there for the
claims current now (#1081), then `.qetl.coverage.compose` and
`.qetl.coverage.gaps`. It is the file and the code a backfill uses to skip a
covered window, so `/coverage` cannot disagree with what a backfill would do.

### The schema it reads

`etl_coverage` is defined in
[`src/etl/core/materialisation.q`](../../src/etl/core/materialisation.q), and
this tree is the authority for it. The block there carries the full column list ---
`dataset`, `partition`, `source_version`, `range_from`, `range_to`,
`rows_published`, `recorded_at`, `superseded_at`, `run_id` --- and
`.qetl.coverage.require_schema[]` confirms the code and the table agree. The
`COVERAGE` program reads the first five.

## Ops views

Three sources, all already reachable, none needing q-side work. Two are read
from the **gateway process itself** rather than routed to a backend tier ---
which still respects the gateway-only boundary, since the gateway is the thing
being asked about.

`/ops/usage` is the interesting one: `.usage.usage` is per-process and **no
fleet-wide rollup exists in q**, so it is fanned out and merged here. The rule
that shaped the design: **one unreachable process must not blank the view.**
Unreachable processes are part of the response, not an error ---

```json
{"row_count": 9, "processes_configured": 10,
 "unreachable": [{"process": "hdb1", "error": "connection refused"}]}
```

`processes_configured` is there because an empty log with nothing configured
looks identical to an idle fleet. Every view also serves its own `poll_seconds`,
because polling is the only mechanism and the right interval depends on how fast
the underlying state moves.

The fan-out targets are the `process.csv` rows `torq.sh start all` starts, read
at startup. Without a stack root there are none --- so the view says it has
nothing configured rather than lying.

### Two live-process findings

- `.gw.servers` is **keyed** by serverid and its `attributes` column holds a
  **dict per server**, which kola cannot serialise at all
  (`Not supported nested list - k type 99`). The program unkeys the table and
  drops that one column; leaving it in fails the entire view for a field no
  dashboard shows.
- **`.usage.flushtime` is one day** (`config/settings/default.q`); a deployment
  may override it. Read it with `ops.FLUSHTIME`.

## Fleet health

`GET /ops/processes` reports every process `process.csv` declares, probed for
liveness. The declared set comes from the **generated** `process.csv` under the
stack root, its `{KDBBASEPORT}+N` resolved against the base port the stack was
started on.

Liveness is an **IPC probe**, so it works whether or not the process is on this
machine. It cannot tell a process never started from one that crashed - both
just don't answer.

### Three states, not two

- **up** --- answered, with its self-reported pid, port and `.proc.procname`
  (`reported_process`).
- **down** --- didn't answer. `down_unexpected` excludes `startwithall=0`
  processes, since those being down is configured behaviour rather than a fault.
  `tap1` is exactly that case, and counting it would make the headline number
  permanently wrong.
- **undetermined** --- the port placeholder couldn't be resolved, so liveness
  was never testable. Reporting that as "down" would be a guess.

### The failure a port check misses

A process answering the right port may be *the wrong process*. Health compares
self-reported `procname` against what `process.csv` declares:

```
port 6053 is answering as 'WRONG1', but process.csv declares 'hdb1' there
```

Verified live. A stale process squatting a port looks perfectly healthy to
anything that only asks whether something is listening. `unknown` (a plain q
process with no `.proc`) is treated as absence of information, not evidence of
the wrong process.

## Usage capture

`.usage.usage` rows are flushed to disk and dropped from memory after
`flushtime`. Any view of error or latency history longer than that window is
therefore **not a query --- it is a capture pipeline**, and it has to run before
the rows are pruned.

```python
capture = UsageCapture(KolaFleet(settings), JsonlSink(Path("captured")))
capture.capture_once()  # safe to call repeatedly, on any scheduler
```

The ordering is deliberately dull and follows the publish-before-checkpoint
rule: fetch everything strictly newer than the watermark, append to the sink,
and advance the watermark **only after the append succeeds**. A failed write
keeps the old watermark so the next pass retries the same rows rather than
losing them, and because the fetch filters strictly greater, a successful pass
captures each row exactly once.

It ships as a callable, not a daemon: what schedules it - a timer, cron, an
Airflow task - is a deployment choice.

## Prepared charts

`charts.py` is a registry of named charts (#2044). Each is defined once there,
and the browser's Charts view holds no chart-specific code: it lists
`GET /charts`, draws a form from each chart's parameter schema, and renders the
figure `POST /charts/{name}` returns. An entry is four things:

- **params** - a Pydantic model the request body must validate against. Unknown
  keys are refused, so a renamed field is a 422, not a default.
- **read** - the vetted query, through a path this API already trusts and
  nothing else. A table goes through `/query`'s own path: the catalog checks the
  table and every filter column, `queries.build_filters` coerces every value,
  the policy is asked about the table, and `.uqf.browse` reads it as the data
  login. The fleet's query log goes through `ops.USAGE`, and backfill status
  through `status.read_dir`. No parameter is rendered into q text.
- **max_points** - the most points the figure may carry, enforced on the server.
  A long series is reduced by LTTB (largest triangle three buckets), which keeps
  peaks, troughs and gaps. Anything that cannot be reduced, such as a venue's
  whole curve or a worker's row in the heatmap, is left out and named in
  `notes`.
- **build** - the figure, built with plotly.py and sent as `fig.to_json()`.

Every response states `point_limit` and `points`. `downsampled` says a series
was reduced; `truncated` says a read hit its row cap (`UQF_FRONTEND_MAX_ROWS`,
or the table's policy) or that series were left out. The reduction is done here,
not with `xbar` in q, because the data login may run `.uqf.browse` and nothing
else (#889). Bucketing in q would need a second gateway function with its own
policy.

  | chart               | reads                                                       | draws                                                     |
  | ---                 | ---                                                         | ---                                                       |
  | `markout_curve`     | `fx_markout` (pips) or `crypto_markout` (bps), `kind` total | mean markout per horizon, a line per venue                |
  | `backfill_coverage` | the status files `/ops/backfill` reads                      | each worker's last run per day, coloured by state         |
  | `mid_spread_fills`  | `market_data`'s first level, and `executions` or `trades`   | mid and spread per `source`, with buys and sells overlaid |
  | `ops_latency`       | `.usage.usage` on every process, as `/ops/usage` does       | completed query durations per process, as box plots       |

## Comparing endpoints

`POST /compare` (#2033) is the Compare view's one call. It names both sides
explicitly - `local` for the stack this frontend serves, or a target in
`deploy_targets.toml` - with an explicit window (`range_from`/`range_to`, each
with its zone), filters, a row limit per side and, optionally, the
`source_version` whose coverage to compare. With a `source_version`, `slice` is
required as it is on `/coverage`, and `""` is the explicit answer for a dataset
with no slice dimension: one omitted is a 422, never the no-slice-dimension
slice by default, and a `slice` without a `source_version` is refused too (#185,
#2221). A comparison that checked no coverage answers `source_version` and
`slice` as `null`. `filters` are the `{column, op, value}` objects `/query`
takes, with `/catalog`'s operators (`in` takes a list): `compare.py` renders
each to the `COLUMN OP VALUE` text `uqs data diff --filter` takes, and the API
does not expose that grammar. A string filter is a 422 saying what it is now,
and a value that cannot be rendered (a comma in a single value, a list for an
operator other than `in`) is refused before uqs is called (#2223). The response
echoes the filters as they were sent. `compare.py` checks that a target side is
registered and hands the rest to `uqs data diff`'s own function
(`uqs.deploy.diff_endpoints.run`): uqs reaches each side's gateway - a target
through an ssh tunnel - and q decides everything the answer says
(src/etl/core/compare.q). A target side is held to what `/services/{target}` and
a target workspace are (#2066): the write token while writes are on (401
`WriteTokenRequired`), an allowed Host, `services:read` (403, audited like any
refused read), and remote operations on - each checked before uqs is called. A
comparison of `local` with itself needs none of these. Each table carries one
outcome - `endpoint_unavailable`, `missing_table`, `differs`, `not_comparable`,
`incomplete_coverage` or `equal_in_sample` - and the label q wrote for it, which
for a sample cut at the row limit says it is not proof the tables are equal.
Every table must be one this stack's catalog lists - an unknown table is the 422
`/query` answers - and is put to the table policy like `/query`'s; each side is
read to at most `UQF_FRONTEND_MAX_ROWS` rows, whatever `limit` asks (#2524). uqs
logs in as the API's service login - the inspection login while inspection mode
uses one - not the `browser` data login, whose role may call only `.uqf.browse`
while a comparison reads through `.gw.syncexec`; so the catalog and the row cap
are what bound it. The route is the `compare` feature's read.

## Backfill status

`GET /ops/backfill` reads the status files q writes, rather than calling
Airflow's REST API. That keeps q authoritative for the facts it owns and adds no
Airflow dependency to a frontend that should work without one. It reads the
directory `.qetl.status.status_dir` writes into (`$UQF_STATUS_DIR`, else
`$TORQDATA/status`), resolved for the stack root.

`.qetl.status.write_status` in `src/etl/core/status.q` defines the format and
`status.py` consumes it; `test_status.py` parses the q source to keep the two in
step. The files carry only what q owns - [the authority
split](../../docs/architecture/pipeline-philosophy.md#8-authority-is-split-and-written-down) -
and a test asserts no Airflow-owned field (`retries`, `try_number`, ...)
appears.

### Three outcomes, not two

`idle` is a **success**, distinct from `completed`: "ran, found no work". The
summary counts `failed` separately from `running`.

Writes are atomic (serialise, temp file, rename), because the frontend polls and
would otherwise be able to read a half-written file. Files ending `.tmp` are
ignored by the reader, and a test covers that.

## Activity history and the action audit

`activity.py` keeps one history with two sources (#1954):

- **server** --- one row for every request that changes something (every method
  but GET, HEAD and OPTIONS, less the reads `POST /query` and
  `POST /activity/events`), written by middleware whether the caller is the UI,
  `curl` or a script. It is written `pending` before the handler runs and
  settled when the response is known, so a request the token or Host check
  refuses is on record too. This is the authority on what happened. A request
  that never passed the write guard - Host or token refused, writes off, or no
  route matched - is kind `attempt` rather than `request`, kept under its own
  cap (#2019).
- **browser** --- what an operator did in the UI, at the level the tab records
  at (below). Intent, sent in batches to `POST /activity/events`.

**Levels (#2000).** Ordered, least detail first; each records its own events and
every level's before it. Every row carries the level it was recorded under,
derived by the server from its kind - the browser cannot send one.

  | level          | records                                                                                                                                                                                                                                                                                    |
  | ---            | ---                                                                                                                                                                                                                                                                                        |
  | `off`          | no browser history. The server still audits every write                                                                                                                                                                                                                                    |
  | `actions`      | `navigate`, `select`, `submit`, `confirm`, `cancel` and the `result` the browser saw, from the handler that performs each - and every server audit row, at any level. The default                                                                                                          |
  | `interactions` | also `interact`: every control used - `ui.click` (a pointer, or Enter or Space on a focused control), `ui.download`, `ui.change` (a select, a checkbox or a field: that it changed, never to what), `ui.toggle` (a section expanded or collapsed) and `ui.copy` - under the control's name |
  | `debug`        | also `debug`: each request's `request.start` and `request.finish` (method, route, status, duration), and a polled view's `resource.poll` and `resource.retry`. Rate-limited in the browser (a burst of 30, then 5 a second)                                                                |

`UQF_FRONTEND_ACTIVITY_LEVEL` sets the default, served on `/health`; an operator
can pick another for one tab in the Activity view (kept in that tab's
sessionStorage). `interactions` is recorded by one listener at the document, not
by any component: a control is named by its `data-activity` attribute, and
`web/src/ActivityCoverage.test.tsx` fails on an interactive element without one -
in the source and on every rendered view - so a new control is covered the day
it is added. The allowlist below applies at every level: a click records the
control's name and kind, a change whether a box is checked, a request its route
(the first two path segments, a name in them replaced by `*`, never the query
string).

  |                         |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
  | ---                     | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
  | `GET /activity`         | The history, newest first. Filters: `since`, `until` (timezone required), `session_id`, `request_id`, `action` (exact, or a dotted prefix: `process` matches `process.stop`), `target` (a deployment target, or `local`), `subject` (what was acted on there), `outcome`, `source`, `limit` (at most 1000). Says whether it is persistent and whether writes have failed                                                                                                                                                                                                                                                               |
  | `POST /activity/events` | Up to 100 browser events, in one transaction, and the count of any the browser did not send (`drops`, with the report's `batch_id`). A repeated `event_id` is ignored, so a retried batch is stored once, and so is a repeated `batch_id`, so its drops are counted once. A body of at most 1 MiB (413). It needs no token; without one, events and drops are rate-limited across all callers (600 at once, 20 a second after; 429, transient). A `ts` UTC cannot hold is a 422                                                                                                                                                        |
  | `GET /activity/export`  | The history as a file, oldest first, under the same filters plus `format`: `jsonl`, `csv`, or `text` - a timeline `UTC time · session · level · action · target · subject · outcome · duration`, each browser action grouped under the server request it caused (its request id). At most 100000 events; one cut at `limit` says so - a text header line, and `X-UQF-Truncated: true`                                                                                                                                                                                                                                                  |

`GET /activity` also takes `level`, the most detailed level shown:
`interactions` shows actions and interactions. `uqs activity list` and
`uqs activity export` read the same file, read-only, with no API running,
through the same `uqs.activity_log` and `uqs.activity_export` the API reads and
writes it with - so the CLI's export is the API's, byte for byte.

**Correlation.** The browser sends `X-UQF-Request-Id` and `X-UQF-Session-Id`
with every write; the server stores both on its row and echoes the request id
(making one up when none, or a malformed one, is sent). The browser's `confirm`
or `submit`, its `result` and the server row of one operation share that id.
Every row has a UTC timestamp, a duration and, for an action over several
processes or files, per-item outcomes. `target` is only ever a deployment
target: a server row's is the path's `{target}`, or `local` for a route that
acts on this server's own stack (control, settings), and a browser row has none.
What the action was on there - a process selector, a `.qetl.cfg` key, a worker,
a view or a control - is its `subject`, and each item names its own `subject`
(#2234). A history file kept before the split is rewritten once when the API
opens it; `uqs activity` refuses one it has not yet opened.

**Outcomes, including the uncertain ones.** `pending`, `succeeded`, `failed`,
`refused` (nothing was acted on: 401, 403, a 422 before acting), `accepted` (a
detached backfill: whether it finished is `/ops/backfill`'s to say) and
`unknown` --- the request had begun to act and then failed, so part of it may
have happened (a `torq.sh` that timed out, a worker-config answer that never
came back, any 5xx). `torq.sh` reports one exit code for a whole selector, so a
failed `stop rdb1 rdb2` marks each item `unknown` rather than guessing which one
failed. A worker-config override shadowed by the environment is `failed`: set,
but not in effect. A row still `pending` when the API restarts is settled
`unknown`. One API process per history file.

**What is never recorded.** Fields are allowlisted, not filtered: a `detail` key
outside `activity.DETAIL_KEYS` is dropped and counted, and each allowed key is
held to a pattern. Nothing reads the `Authorization` header, a request body, a
typed value (a filter value, a config value: the field and key are recorded, the
value is not) or a file's contents. An error is recorded by its type, never its
message, which can quote the input back. `test_activity.py` searches the
database for the write token and for typed values.

**Identity.** `claimed_user` is the user a trusted proxy named, when one did
(see the access policy below), and a server row's `identity_source` detail says
`verified`. Otherwise it is the `x-uqf-user` header as sent --- a label anyone
can set, stored with `identity_source: claimed` and never read as who called. A
history file kept before `identity_source` had that name is rewritten once when
the API opens it.

**Storage and retention.** SQLite, from the standard library: the filters need
an index rather than a scan of every line, and retention needs a `DELETE` rather
than a rewrite, which rules out the JSON Lines `capture.py` uses for its
append-only feed. The file is `$TORQDATA/frontend/activity.sqlite3` for the
stack `UQF_FRONTEND_STACK_ROOT` names, beside the workers' status files;
`UQF_FRONTEND_ACTIVITY_DB` names another. `uqs clean` removes `$TORQDATA`, this
file with it; put it elsewhere to keep history past a clean. With neither set,
history is kept in memory and `GET /activity` says it is not persistent. Each
level has its own bounds, applied at startup and every 200 writes: `actions` -
which holds the server's audit - keeps 30 days and 100000 rows
(`UQF_FRONTEND_ACTIVITY_RETENTION_DAYS`, `UQF_FRONTEND_ACTIVITY_MAX_KEPT`),
`interactions` 7 days and 50000, `debug` 1 day and 10000
(`UQF_FRONTEND_ACTIVITY_INTERACTIONS_*`, `UQF_FRONTEND_ACTIVITY_DEBUG_*`). A
flood of clicks therefore never evicts an audit row. Within a level, browser
rows and the server's audit are capped separately, and `attempt` rows have a cap
of 1000 of their own, so neither the browser's events nor a stranger's refused
requests can evict the audit of an authorised action (#2019).

**Nothing is lost without a number.** What a level's row cap evicts (`row_cap`),
what ages out (`retention`), what the browser's debug rate limit sampled away
(`sampled`) and what overflowed its queue while the API was unreachable
(`queue_overflow`) are counted by level in `activity_drops`, shown on
`GET /activity` and the Activity view, and stated at the head of a text export.
A batch of browser events is one transaction, and the file runs in WAL mode with
`synchronous=NORMAL`: a power cut can lose the last writes, never the file.
`test_activity_levels.py` holds a history written at click rate to its cap:
10000 clicks against a cap of 2000 leave 2000 rows, the file stops growing after
the first prune, and a batch of 100 costs about a millisecond.

**When persistence fails.** A configured file that cannot be opened stops the
API at startup. After that, a failed write never blocks the action it audits:
the action goes ahead, its response carries `X-UQF-Audit: unrecorded` (else
`recorded`), the failure is logged, and `GET /activity` reports the count and
the last error, which the Activity view shows.

This is operational logging: it helps an operator answer "who asked for what,
and what came of it". It is not tamper-proof compliance evidence --- anyone with
the file can edit it --- and not a recording of the screen.

**Adding an action** (uploads, #1951; deletions, #1953). A new state-changing
route is audited without doing anything, under a generic `http.<method>` name,
and `test_activity.py` fails until it has an entry in `activity.SERVER_ACTIONS`.
Its handler then reports what the status code cannot:

```python
audit = activity.audit_of(request)
audit.set_subject(name)  # written at once: survives a crash mid-action
audit.note(path=path, size=size, sha256=digest)  # never the bytes
audit.begin()  # past here, a failure is `unknown`
...
audit.item(name, "succeeded")  # one per file, for a batch
audit.settle("failed")  # only where the status code would mislead
```

## Identity and the access policy

Two layers decide every request, both in `app.guard`, and both default to
allowing everything --- so a deployment that configures nothing behaves exactly
as it always did. They are additional to the write token and the Host check,
never instead of them: those still stand in front of every write.

**Who is calling** (`identity.py`, #2045). The frontend authenticates nobody
itself. An authenticating proxy in front of it --- oauth2-proxy, Authelia,
Tailscale serve --- does, and names the user in a header. That header is
believed only when `UQF_FRONTEND_TRUSTED_IDENTITY_HEADER` names it **and** the
request's direct TCP peer is in `UQF_FRONTEND_TRUSTED_PROXIES`. From any other
address, sent twice, or not shaped like a user name, it is ignored and the
caller is `anonymous`. A spoofed header from a caller that reached the port
directly is therefore just a header. The old `x-uqf-user` header is a claim and
never reaches a policy; the activity history still records it, labelled
`claimed`.

**What they may do** (`access.py`). A Casbin RBAC policy (pycasbin,
`policy/model.conf`) over features --- one per view: `coverage`, `backfills`,
`desk`, `charts`, `compare`, `fleet`, `queue`, `connections`, `usage`,
`services`, `control`, `activity`, `settings` --- and their actions, `read` and
`write`. `access.ROUTE_PERMISSIONS` is the one place a route is tied to a
feature; a route missing from it is refused to everyone, and a test fails on it.
A refusal is 403 and audited: a refused write settles its own audit row with the
permission it lacked, and a refused read gets an `authz.refused` row.

```text
# UQF_FRONTEND_POLICY_FILE - Casbin CSV
p, viewers, desk, read          # grant: subject, feature, action (* allowed)
p, viewers, coverage, read
p, operators, control, *
p, admins, *, *
p, anonymous, coverage, read    # callers no proxy vouched for
g, alice, admins                # membership: user (or group), group
g, bob, viewers
```

The packaged default is `p, *, *, *`. A named policy file is read strictly and
re-read when it changes, without a restart; an edit that does not parse is
logged and the last good policy stays in force.

**`GET /me/capabilities`** answers with the caller's `user` (or `anonymous`),
groups and granted features. The browser shows only the views the caller may
read and disables what they may not change, through one context
(`web/src/capabilities.tsx`). That is presentation: the server refuses whatever
the policy does not grant, whether or not the button was on screen.

The table/path policy is the older seam, still there for a rule that keys on a
table rather than a feature: `create_app(policy=deny_tables({"position"}))` is a
403 on that table from `/query` and `/coverage` alike, before any data is read.

### The Settings view: changing the policy and runtime settings

The Settings view (#2046) edits the access policy and the runtime settings ---
the default activity level, activity retention, config push history --- in forms
react-jsonschema-form renders from JSON schemas the server builds from its own
Pydantic models (`GET /settings`), so the browser keeps no list of fields. It is
loaded only when opened.

  |                         |                                                                                                                               |
  | ---                     | ---                                                                                                                           |
  | `GET /settings`         | `settings:read` --- the policy, runtime settings, administrators, which secrets are set (never their values), and the schemas |
  | `PUT /settings/access`  | `settings:write` --- replace the policy                                                                                       |
  | `PUT /settings/runtime` | `settings:write` --- replace the runtime settings                                                                             |

A change needs writes on, the write token and an allowed Host like every other
write, and `settings:write` on top. It is validated by the model (extra keys
refused) and again by the server's own checks, refused with 409 if the version
it was edited from is no longer current, or if it would leave nobody able to
change the policy again. It is then written atomically (temporary file, fsync,
rename), in force at once without a restart, and audited as `settings.access` or
`settings.runtime` with its before/after diff in the `changes` detail. The
policy is kept in `UQF_FRONTEND_POLICY_FILE` and the runtime settings in
`UQF_FRONTEND_SETTINGS_FILE`; without either, that part is read-only. Settings
that hold a secret --- the write token, the gateway and data passwords --- are
never editable or sent: the view says only whether each is set.

### Running behind oauth2-proxy

oauth2-proxy authenticates against your identity provider and forwards to the
frontend with `X-Forwarded-User` (and, with groups, `X-Forwarded-Groups`) set ---
replacing anything the client sent. Bind the frontend where only the proxy can
reach it, and tell it to believe the proxy and nobody else:

```bash
# the frontend: loopback only, believing the proxy on 127.0.0.1
export UQF_FRONTEND_TRUSTED_IDENTITY_HEADER=x-forwarded-user
export UQF_FRONTEND_TRUSTED_GROUPS_HEADER=x-forwarded-groups
export UQF_FRONTEND_TRUSTED_PROXIES=127.0.0.1
export UQF_FRONTEND_POLICY_FILE=/etc/uqf/policy.csv   # copy policy/default_policy.csv, then edit
export UQF_FRONTEND_ALLOWED_HOSTS=uqf.example.com     # the name the browser uses, with writes on
# --no-proxy-headers: the peer the frontend checks must be the proxy itself,
# not an address uvicorn read out of X-Forwarded-For
uv run uvicorn --factory uqf_frontend.app:create_app --host 127.0.0.1 --port 8000 --no-proxy-headers

# the proxy, in front of it (secrets from the environment, never the command line)
oauth2-proxy \
  --http-address=0.0.0.0:4180 \
  --upstream=http://127.0.0.1:8000/ \
  --provider=oidc --oidc-issuer-url=https://id.example.com \
  --email-domain=example.com \
  --pass-user-headers=true
# OAUTH2_PROXY_CLIENT_ID, OAUTH2_PROXY_CLIENT_SECRET and OAUTH2_PROXY_COOKIE_SECRET
# are read from its environment
```

Then check it from a shell on another machine: a request straight to port 8000
must not be reachable at all, and one through the proxy should show your user:
`curl -b <session cookie> https://uqf.example.com/me/capabilities`. If it says
`anonymous`, the peer the frontend saw was not `127.0.0.1` --- usually uvicorn
rewriting it from `X-Forwarded-For`, which `--no-proxy-headers` stops.

## Inspection mode

A browser on a workstation that reaches production through ssh tunnels gets the
same routes as one on the production host. Hiding the buttons refuses nothing,
because a direct request still reaches the route. Inspection mode is therefore a
policy of the server, enforced in `app.guard` (`inspection.py`, #2032):

- **Writes are refused.** Every route that `access.ROUTE_PERMISSIONS` maps to a
  `write` action is a 403 `InspectionMode`, whoever asks and whatever the access
  policy grants them. That covers lifecycle start, stop and restart, backfills,
  process and worker config writes, uploads, deletes, and config pushes and
  restores. A write route added later is mapped before it can be reached at all,
  so it is refused without a list of its own here. The refusal is audited like
  every refusal: the write's own audit row settles `refused`, with
  `permission: inspection` and `error: InspectionMode`.
- **Reads stay available.** That includes `/query`, the ops views, Services, the
  config push history and a restore's plan.
- **One exemption.** `POST /activity/events` is the browser recording its own
  history in this frontend's database. It changes nothing on a stack or server.
  `inspection.EXEMPT` holds it with that reason, and a test holds the list to
  exactly it.
- **The other checks still apply.** The write token and the Host check run
  first, as before. `test_inspection.py` calls every write route directly, with
  the token and an allowed Host, under a policy that grants everything. It
  checks that each one is refused and audited, and that nothing reaches ssh.

It has two scopes:

  | Scope           | Turned on by                                                                                  | Refuses                                     |
  | --------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------- |
  | the frontend    | `UQF_FRONTEND_INSPECTION_MODE=1`                                                              | every write                                 |
  | one target      | `inspection = true` on it, or its environment, in `deploy_targets.toml` (`uqs`'s registry)    | every write naming that target              |

The registry is read on each write, so a target put in inspection mode is
refused from the next request, with no restart. If the registry cannot be read,
every write naming a target is refused, because its mode is unknown. `uqs`
refuses the same target's changes itself (`uqs.deploy.inspection`; see the
[deploy guide](../../docs/guides/deploy.md#inspection-only-targets)). A write
naming another declared target at the same server (the same ssh and `dest`,
compared as `uqs` compares them) is refused as that target's.

**Workspaces (#2029).** The target a request acts on is its `{target}` path
parameter. Otherwise it is the workspace named in the request's `X-UQF-Target`
header, resolved by `workspaces.requested`, the same way the scoped routes
resolve it. The guard checks this before `workspace_route` turns an unscoped
route away from a target with 409. A write sent under an inspection-only
workspace is therefore a 403 `InspectionMode`, audited, and nothing connects to
the target. Reads under that workspace go on as before. The local workspace
follows `UQF_FRONTEND_INSPECTION_MODE` only.

**What the browser is told.** `GET /me/capabilities` reports the effective
policy. With the whole frontend in inspection mode, `capabilities` leaves out
every write it refuses, and `inspection` says:

```json
{"active": true, "targets": ["prd"], "q_login": "inspection", "registry_error": null}
```

The UI shows an "Inspection mode" banner from this, and the Control view says
inspection mode rather than "not part of your access". `targets` and
`registry_error` are given only to a caller who may read the Services view.

**What it is not.** Neither ssh nor a tunnel becomes read-only. The frontend and
`uqs` refuse the change, but anyone who can ssh to the server, or who holds a q
login with write access, still can make it. The banner says so, and no part of
the UI claims otherwise.

### Restricted q logins

The frontend connects to q with two logins:

  | Login                                                     | Used for                                                                                     | Read-only?                                                                                                                                                  |
  | --------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | data, `UQF_FRONTEND_DATA_USER` / `_PASSWORD`              | `/query` table reads, through `.uqf.browse`                                                  | yes, already: the `browser` role (`scripts/torqconfig/permissions/gateway.q`) may run `.uqf.browse` and `.checkinputs.querypolicyfor` and nothing else      |
  | service, `UQF_FRONTEND_GATEWAY_USER` / `_PASSWORD`        | the catalog, coverage, the ops views, health, the per-process fan-out, and worker config     | no: `admin` on the demo stack, the TorQ `administrator` role                                                                                                |

In inspection mode, `UQF_FRONTEND_INSPECTION_USER` / `_PASSWORD` replaces the
service login for the gateway and for the per-process fan-out.
`/me/capabilities` reports which login is in use (`q_login`). Setting it without
inspection mode refuses to start, because nothing would use it. The data login
is unchanged.

The q side has to make that login read-only. The frontend cannot. No role for it
ships with the demo stack, and a deployment adds one. TorQ's permissions
(`lib/torq/code/handlers/permissions.q`) give two tools, and only the second
holds for everything:

1. **A role granted named read functions only.** For example, in the
   deployment's gateway permissions file, next to `browser`:

   ```q
   .pm.addrole[`inspector;"the frontend's own reads in inspection mode (#2032)"]
   .pm.grantfunction[;`inspector;{1b}] each
     `.qcat.surface`.gw.getqueue`.qetl.coverage.claims`.qetl.coverage.ready_claims`.qetl.coverage.compose
   ```

   Add the user to the deployment's `gateway_users.csv` with role `inspector`,
   and to each process's access list for the fan-out. Never grant it
   `.gw.syncexec`, `select` or `*`. **A role alone is not read-only, though.**
   TorQ runs a lambda for any role once the variables it reads are readable
   (`.pm.lamq`), and it checks no write the lambda makes. Views that send a
   lambda or an expression (the schema, the gateway's server and client lists,
   the usage log, coverage gaps) are refused to the role, or run with only that
   check.

2. **A read-only process.** `.readonly.enabled:1b` makes TorQ evaluate every
   client query on that process with `reval`, which refuses any write. It is
   process-wide, so set it on a gateway that only inspection traffic uses, not
   on the production gateway that writers share.

For a production that must not be changed through this frontend, use both: an
`inspector` login, on a read-only gateway. Until that is in place,
`q_login: "service"` in `/me/capabilities` (and the banner) says the frontend
still reads as its ordinary login.
