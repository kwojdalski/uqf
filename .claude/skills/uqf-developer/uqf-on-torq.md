# How uqf sits on TorQ

Read this before touching anything a TorQ process loads: `process.csv`,
`database.q`, the gateway, the overlays under `scripts/torqconfig` and
`scripts/torqcode`, a runtime, a profile, an attachment, a deployment or a
sidecar bundle. For TorQ itself (its namespaces, startup, EOD, gateway
internals) read the `torq-developer` skill; this page says only where uqf
differs from or layers over it.

## 1. The rule

`lib/torq` (TorQ 5.2.12, DataIntellectTech @a6cee6ce) and
`lib/torq-finance-starter-pack` (@50fcd5ad) are **never edited**. Every tree
under `lib/` is recorded in `lib/VENDORED.toml` with blob listings in
`lib/.vendored/<tree>.tsv`; `check.py vendored` fails a byte that differs, and
`scripts/dev/revendor.py TREE [--commit SHA | --verify]` is the only way a tree
changes. uqf extends TorQ through:

1. the generated `process.csv` and `database.q` under `$TORQDATA`;
2. the service-layer overlays `KDBSERVCONFIG=scripts/torqconfig` and
   `KDBSERVCODE=scripts/torqcode`;
3. files added to a vendored process's `load` column;
4. `process_overrides.csv`, field by field.

Other vendored trees: `qAutomatedTrading`, `ml` (KX), `kdb-parquet`,
`kx-dbmaint`, `unparse` (loaded only by `src/etl/core/qsql_text.q` when
present), `kdb-fix-adaptor`. Licences are in `LICENSING.md`. **Vendor, don't
reinvent:** when working q already exists upstream, vendor it unchanged under
`lib/` (record its licence; surface a missing one to the user rather than
deciding) and wrap it - compression uses TorQ's `.cmp` through
`scripts/processes/torq_compress.q` rather than a rewrite.

"TorQ runs unchanged" is true of the bytes and of the `torq` runtime. In the
`uqf` runtime its processes are layered over: stp1 loads a generated schema,
gateway1 carries query policies and the catalog, the sorters number rows,
`.servers.register` and `.servers.loadpassword` are wrapped.

## 2. The environment - `python/uqs/src/uqs/stack/env.py`

`build_env()` is pure; `write_setenv` writes `$TORQDATA/setenv.sh` and points
`SETENV` at it so torq.sh does not source `lib/torq/setenv.sh`. `uqs config env`
prints the resolved set.

  | Variable                       | Value                                                                                                                        |
  | ---                            | ---                                                                                                                          |
  | `TORQHOME`, `TORQAPPHOME`      | `lib/torq`, `lib/torq-finance-starter-pack` (or the caller's `$TORQHOME`/`$TORQAPPHOME`, validated by `torq.q`/`database.q`) |
  | `TORQDATA`                     | `output/uqs` for `uqf`, `output/uqs-<runtime>` otherwise (or under `$UQS_DATA_ROOT`)                                         |
  | `UQF_ROOT`, `UQF_SCRIPTS`      | the repo root and `scripts/` (or `<data>/qtree` for a flattened runtime)                                                     |
  | `KDBCONFIG`, `KDBCODE`         | `$TORQHOME/config`, `$TORQHOME/code`                                                                                         |
  | `KDBSERVCONFIG`, `KDBSERVCODE` | `scripts/torqconfig`, `scripts/torqcode` - **deleted when the runtime has no overlays** (`torq`, `peachq`)                   |
  | `KDBAPPCONFIG`, `KDBAPPCODE`   | `$TORQAPPHOME/appconfig`, `$TORQAPPHOME/code`                                                                                |
  | `KDBLOG`                       | `$TORQDATA/logs` (not `KDBLOGS`)                                                                                             |
  | `KDBHDB`, `KDBWDB`, `KDBTPLOG` | `$TORQDATA/hdb`, `/wdbhdb`, `/tplogs`                                                                                        |
  | `KDBBASEPORT`                  | the runtime's base port, or `--base-port`                                                                                    |
  | `TORQPROCESSES`                | `$TORQDATA/process.csv`                                                                                                      |
  | `UQF_STATUS_DIR`               | the caller's, else `$TORQDATA/status`                                                                                        |
  | `UQF_DEPLOYMENT_FILE`          | `$TORQDATA/deployment.txt`                                                                                                   |
  | `QCMD`                         | the caller's `$QCMD`, else `q`; a PeachQ runtime sets it to the pinned PeachQ                                                |

TorQ layers them (`lib/torq/torq.q`): settings load KDBCONFIG -> KDBSERVCONFIG
-> KDBAPPCONFIG, each `default`, `parentproctype`, `proctype`, `procname`; code
and handlers load KDBCODE -> KDBSERVCODE -> KDBAPPCODE. **A config FILE lookup
takes the first hit in app, serv, base order**, so a CSV in the starter pack's
`appconfig` shadows one in `scripts/torqconfig`. That is why uqf copies the
compression, housekeeping, reporter and DQE CSVs into `$TORQDATA` and points
TorQ at them with the `scripts/processes/uqs_*_config.q` loaders.

## 3. The overlays

  | Path                                                            | What it does                                                                                                                                                                                                                                                                                                           |
  | ---                                                             | ---                                                                                                                                                                                                                                                                                                                    |
  | `scripts/torqconfig/settings/default.q`                         | every process: TorQ's `INF/WARN/ERR` renamed to `INFO/WARNING/ERROR` to match `.qetl.log`                                                                                                                                                                                                                              |
  | `scripts/torqconfig/settings/gateway.q`                         | `.pm.enabled:1b`; `.checkinputs.querypolicypath`; `.checkinputs.policyceiling` (31D, 5M rows, 128MB, 5 min); trusted roles                                                                                                                                                                                             |
  | `scripts/torqconfig/dataaccess/querypolicy.csv`                 | per-table query policies and role exceptions (`docs/architecture/query-policies.md`)                                                                                                                                                                                                                                   |
  | `scripts/torqconfig/dataaccess/tableproperties.csv`             | TorQ's `-dataaccess` table list; generated (`derive.py table-properties`), copied per runtime into `$TORQDATA`                                                                                                                                                                                                         |
  | `scripts/torqconfig/permissions/`                               | roles `analyst`, `quant`, `browser` (only `.uqf.browse` and `.checkinputs.querypolicyfor`); `gateway_users.csv`, `subscriber_users.csv`                                                                                                                                                                                |
  | `scripts/torqconfig/compressionconfig.csv`, `housekeeping*.csv` | generated renderings (`derive.py compression-config`, `housekeeping-config`); tplogs are zipped, never deleted                                                                                                                                                                                                         |
  | `scripts/torqconfig/sort.csv`                                   | wdb/sort rules (`p#sym`; `market_data` by sym, source, ...)                                                                                                                                                                                                                                                            |
  | `scripts/torqconfig/sources.csv`                                | header only; the operator's rows go in the application layer's `lib/torq-finance-starter-pack/appconfig/sources.csv` - gitignored, not part of the vendored tree, written by `uqs config sources stub SOURCE`; it wins over the service layer's a whole file at a time (`python/uqs/src/uqs/stack/source_settings.py`) |
  | `scripts/torqcode/handlers/loadpassword.q`                      | replaces `.servers.loadpassword`, which drops the base password files once a third config layer exists                                                                                                                                                                                                                 |
  | `scripts/torqcode/handlers/deployment.q`                        | drops process.csv rows not in `$UQF_DEPLOYMENT_FILE` from discovery                                                                                                                                                                                                                                                    |
  | `scripts/torqcode/handlers/pmusers.q`                           | access-list logins are administrators; `<proctype>_users.csv` users get their role                                                                                                                                                                                                                                     |
  | `scripts/torqcode/gateway/querypolicy.q`                        | wraps `.checkinputs.checkinputs` with the policy and `.dataaccess.autojoin` with a response-size check                                                                                                                                                                                                                 |
  | `scripts/torqcode/gateway/browse.q`                             | `.uqf.browse[t;fc;fo;fv;lim;procs]`: the browser's filtered getdata, under its policy                                                                                                                                                                                                                                  |

The gateway's read path: client -> `.dataaccess.getdata` ->
`.checkinputs.checkinputs` (TorQ's checks, then the policy) -> rdb/hdb getdata
-> autojoin. Ordinary users log in only at gateway1. The desk catalog is
`scripts/processes/uqs_catalog.q` (`.qcat`): every published table is in
`.qcat.describe` or `.qcat.hidden`, and a whole-table select needs a
`.qcat.unbounded` reason or a `querypolicy.csv` row (`tests/q/test_catalog.q`).

## 4. `process.csv` - `python/uqs/src/uqs/stack/procs.py`

`effective_process_rows()` composes it fresh, then `stack/generated.py`
`write_process_csv()` writes `$TORQDATA/process.csv` (atomic, `\n` line ends
because torq.sh's awk breaks on `\r`, under the `.lifecycle.lock` flock):

1. the starter pack's `appconfig/process.csv`, read fresh, never edited (23
   rows: stp1 +0, discovery1 +1, rdb1 +2, hdb1 +3, hdb2 +4, wdb1 +5, sort1 +6,
   gateway1 +7, killtick +8, monitor1 +9, tpreplay1 +10, housekeeping1 +11,
   reporter1 +12, filealerter1 +13, feed1 +14, sctp1 +15, sortworker1/2 +16/+17,
   metrics1 +18, dqc1/dqcdb1/dqe1/dqedb1 +20..+23);
2. with overlays: `VENDORED_STARTWITHALL_OVERLAY` (monitor1 on, feed1 off),
   `VENDORED_LOAD_OVERLAY` (extra load files on gateway1, hdb1, dqe1, reporter1,
   the sorters, housekeeping1), `-dataaccess ${TORQDATA}/tableproperties.csv` on
   every gateway/rdb/hdb, the generated access lists on gateway1 and sctp1, and
   stp1's `-schemafile` pointed at `${TORQDATA}/database.q`;
3. the pipeline rows (`model/pipelines.py`): one per process in `PIPELINES`
   (`model/registry.py`, built from the q declarations plus `NON_JOB_PIPELINES` =
   `tap1`), port `{KDBBASEPORT}+<offset>` from
   `scripts/processes/process_ports.csv`, load
   `${UQF_SCRIPTS}/processes/torq_pipeline.q` then the runner; proctype `feed`,
   `backfill` or `metrics` (an etl or normalizer borrows the access-listed
   `metrics` proctype);
4. `process_overrides.csv` last, field by field.

`process_ports.csv` holds only uqf's own processes (52 rows); the vendored ports
are the starter pack's. It is an append-only lock written by
`derive.py operational-docs`: never renumber a row.

`process.csv` and `database.q` are rewritten whenever a command bootstraps -
start, restart, debug, `exec`, `job backfill`, `data replay`, `data seed`.
`uqs stop` and `uqs status` do not bootstrap. Never edit either by hand.

### Process overrides

`uqs config set PROC FIELD VALUE` writes `python/uqs/process_overrides.csv`
(gitignored, `procname,field,value`, one file shared by every runtime);
`uqs config unset`, `config show`, `config list`, `config field list` read and
undo it. Unknown fields and unsafe values are refused. A bundle's own overrides
go to `python/uqs/bundle_overrides.csv`; host rows win. On a server the file
lives in `shared/config/python/uqs/` and survives releases. The frontend's
`PUT /control/processes/{p}/config` and the MCP `uqs_config_set` tool use the
same writer.

## 5. `database.q` - the plant schema

Only stp1 (the segmented tickerplant) loads a schema file. With overlays,
`$TORQDATA/database.q` is the vendored `database.q` plus every table in
`src/etl/plant_tables.q` (filtered to the runtime's tables), built by
`model/plant_schema.py`; without overlays it is a verbatim copy. A table must be
defined there before anything publishes to it (`uqf-etl-framework.md` section
4).

## 6. The processes

- **Vendored, layered:** stp1 (schema), gateway1 (catalog, coverage modules,
  policies), rdb1/hdb1/hdb2 (`-dataaccess`; hdb1 also loads `metatables.q`,
  `mapped_books.q`, `mapped_depth.q`), wdb1/sort1/sortworker1/2 (`uqs_row_ids.q`
  numbers `market_data`'s `rid`), housekeeping1 (zip-not-delete rules,
  compression), dqe1 and reporter1 (TORQDATA configs), monitor1 (its own handle
  budget). sctp1 is the chained tickerplant real-time subscribers attach to
  (`docs/guides/real-time-subscribers.md`).
- **uqf scripts** in `scripts/processes/`: `torq_pipeline.q` (`.qtorq`, first in
  every pipeline row), `torq_stream.q`, `torq_backfill.q`, `torq_tap.q` (tap1, a
  debug tap), `torq_compress.q`/`torq_compress_survey.q` (over `.cmp`),
  `torq_metatables.q` (DQE adapter), `uqs_catalog.q`, the `uqs_*_config.q`
  loaders, `uqs_row_ids.q`.
- The full list with ports and edges is the generated
  `docs/reference/processes.md` (23 vendored + 44 uqf); the prose is
  `docs/architecture/stack.md`; each process's card is `docs/services/`.

## 7. Runtimes and profiles

A **runtime** is which stack exists; a **profile** is which part of it starts.
`python/uqs/src/uqs/runtimes.py` (`RUNTIMES`), selected by `uqs --runtime NAME`
or `UQS_RUNTIME`:

  | Runtime         | Base | Pipelines | Overlays | Notes                                                                              |
  | ---             | ---  | ---       | ---      | ---                                                                                |
  | `uqf` (default) | 6050 | yes       | yes      | the starter pack plus all of uqf (67 processes, 45 tables)                         |
  | `torq`          | 6150 | no        | no       | the starter pack exactly as shipped - tells TorQ's problems from uqf's             |
  | `peachq`        | 6450 | no        | no       | the starter pack as shipped, on PeachQ                                             |
  | `crypto`        | 6250 | yes       | yes      | the crypto profile's pipelines and tables                                          |
  | `fx`            | 6350 | yes       | yes      | the fx profile's pipelines and tables                                              |
  | `peachq-etl`    | 6550 | yes       | yes      | experimental: peachq's capture stack plus declared bundles' jobs, flattened q tree |

`uqs runtime list` is authoritative; `uqs runtime diff A B` compares two.

Profiles (`model/profiles.py`) name leaves; starting one pulls in everything it
reads, plus the infra (`model/infra.py`). `uqf`'s are `default`, `fx`,
`arbitrage`, `depth`, `crypto`, `essential` and `all` (`all` does not fit the
licence). A standing process no profile reaches is listed in
`model/unprofiled.py` with its reason, and `test_profiles.py` fails a new one in
neither. `uqs profile list` shows each profile's processes and slots.

**The connection budget.** A KDB-X community licence allows 16 concurrent
connections to one q process; 2 are held back (`model/pipeline_edges.py`:
`LICENCE_CONNECTION_LIMIT = 16`, `INBOUND_RESERVE = 2`), so 14 plant slots. What
spends a slot is a **tickerplant subscriber**: the started non-backfill
pipelines plus the vendored plant clients rdb1, wdb1, sctp1 and metrics1 - not
every process (`essential` is 14 processes but 4 slots). Past the cap the plant
resets the extra handles while `uqs status` still shows them up.
`UQS_LICENCE_CONNECTIONS` overrides the limit; PeachQ has none. Don't give a job
`start_with_all 1b` unless asked (`uqf-q-rules.md` J14). (`docs/faq.md` says
"every started process"; the code counts subscribers.)

## 8. Attachments - a uqf job on someone else's TorQ

`python/uqs/src/uqs/attach/`, guide `docs/guides/attach-existing-torq.md`. An
attachment runs a uqf job against a TorQ stack uqf does not manage: plan ->
validate (preflight) -> apply -> verify -> detach, from a write-ahead manifest,
never restarting a process it did not start.

- `uqs job attachment add NAME --job JOB --base-port N ...` (NAME is the
  attachment's, e.g. `piggybank-uat`) replaces the old stage commands; also
  `remove`, `show`, `list`, `status`, `contract export|approve|list`.
  `uqs job backfill --attachment` backfills into the attached stack's HDB.
- `attachment.json` holds the stack id (its base port), discovery, tickerplant,
  consumers and the password **variable name**, never the secret.
- Preflight blockers: per-table tplog logging, unfiltered consumers, a sort.csv
  that can sort the table, an appendable schema file; supported TorQ is
  `SUPPORTED_TORQ = ("5.2.12",)`. A finding is overridden per component
  (`--override TORQ_VERSION:stp1 --reason ...`) and voided when facts change.
- Ownership is `created`, `existing` or `shared`; detach removes only what it
  created. `verified` is the only state in which the publisher may start.
- `--process-file PATH` (native registration) adds one row to the site's own
  process.csv and a gated launcher. The q sent over IPC is in `attach/q/`; the
  backfill side is `scripts/jobs/attached_*.q`.
- `live_stack` tests (`-m live_stack`) exercise it on isolated stacks; never run
  them unasked.

## 9. Deployments and targets

`python/uqs/src/uqs/deploy/`, guide `docs/guides/deploy.md` (not yet run end to
end). Deployment targets Linux.

```bash
uqs deploy build [--q-version 4.0] [--bundle DIR]          # an artifact from a commit
uqs deploy push dist/uqf-<release>.tar.gz TARGET           # a target in deploy_targets.toml
uqs deploy status|history|rollback|prune TARGET
uqs deploy component build torq|q|python                   # bundled components
uqs --target TARGET status                                 # any command, on a deployed release, over ssh
```

- A push runs transfer -> prepare (an offline venv from the artifact's wheels)
  -> HDB check -> smoke (`scripts/deploy/smoke.q`) -> stop the previous release
  -> port check -> start -> verify -> live check / soak -> activate `current`;
  any failure rolls back and writes a report into the release.
- TorQ, q and Python can be the server's (`existing`) or shipped
  (`--torq-mode`/`--q-mode`/`--python-mode bundled`).
- `--q-version 4.0` flattens the release's q with
  `scripts/portable/flatten_contexts.py`; `--override accept-converted-release`
  exists for an older server q.
- Targets: `deploy_targets.toml` at the repo root (or `$UQS_DEPLOY_TARGETS`),
  gitignored, layered `[defaults]` -> `[environments.NAME]` -> target -> flags;
  managed with
  `uqs target add|set|unset|rename|remove|edit|list|check|log|revert|adopt|shell|tunnel|config`.
- The `security-auditor` agent reviews what runs on a remote target.

## 10. Sidecar bundles and connectors

- `sidecars/` holds bundles installed only on request: `mockups`, `cryptorust`,
  `databento`, `flink`, `kafka_example`, `kafka_crypto_example`. Each has
  `bundle.json`, `tables.q`, `catalog.q`, job files, `test_*.q`, and optionally
  `process_overrides.csv` and Python connectors
  (`docs/guides/sidecar-bundles.md`, `docs/architecture/bundle-overlay.md`).
- `uqs job install BUNDLE` / `uqs runtime prepare` write tracked files (job
  files, `/ BEGIN bundle` blocks in `plant_tables.q` and `uqs_catalog.q`,
  `src/etl/installed_bundles.json`). **Never run either in this checkout or a
  worktree** (`--dry-run` is fine): `.claude/hooks/no_bundle_install.py` blocks
  them, both refuse a development checkout without a `.uqf-instance` marker, and
  `check.py bundle-blocks` refuses a commit carrying their output. Test a bundle
  with `uv run python scripts/dev/bundle_suite.py <bundle>`; ship one with
  `uqs deploy build --bundle <bundle>`.
- `uqs connector start|stop|restart|status|logs CONNECTOR` runs the processes
  outside the stack: `crypto`, `crypto-fills`, `kafka`, plus any a bundle
  installs (`databento`, `flink_vwap` exist only once their bundle is
  installed). Code: `python/uqs/src/uqs/external/`.

## 11. TorQ and KDB-X behaviour this tree has paid for

Each is encoded in a comment at the cited line; `uqf-q-traps.md` has the nine
`.qtorq` invariants with reproductions.

- `.u.upd` stamps its own `time` (`lib/torq/code/segmentedtickerplant/stplog.q`
  `updtab`); a publisher that sends one makes every row one column too wide, and
  the tickerplant logs "Bad message received, error: length" at **INFO** and
  drops it. When a table stays empty with no client error, grep stp1's out log.
- `.[f;();h]` fires `h` even when a niladic `f` succeeds; use `@[f;::;h]`
  (`torq_pipeline.q:35-46`). `.[f;enlist ::;h]` throws an uncatchable `'type`
  (`src/etl/core/log.q:347-350`).
- A timer function that throws is deactivated by `.timer` - always trap it
  (`.qtorq.safe_timer`).
- torq.sh exits 0 on an unknown process name, so `uqs` validates names first
  (`stack/procs.py`); q exits 0 whatever happens while loading a script, so
  loaders use sentinels.
- `endofperiod`/`endofday` must exist at root on every subscriber
  (`.qtorq.install_period_handlers`).
- Hand-run q scripts on this Mac have received no async pushes from a TorQ
  tickerplant; to test a subscriber, use a kxkdb/kola client or query the TP's
  `.stpps`/`.stplg` state instead of trusting a script's `upd` count.
- Hostnames: q's `.z.h` is lower-case and `gethostname()` on this Mac is not;
  compare hosts case-insensitively (`python/uqs/src/uqs/liveness.py`).
