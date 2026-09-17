# Environment variables

The one list C-04 asked for. Hand-written, because "required or optional",
"what happens when it is unset" and "who sets it" are judgements no generator
can read off the code — and machine-checked for completeness by
`scripts/check_env_reference.py`, so it cannot quietly fall behind.

Read this table as the operator's surface. Two things make it non-obvious
without it:

- **One variable is built mechanically and appears nowhere in the source.**
  `.qwcfg.env_name` maps a config key to `UQF_` plus the upper-cased key, so
  `UQF_DRY_RUN` is read by `.qwrt.is_dry_run` without the string
  `UQF_DRY_RUN` existing in any production file. `grep` cannot find it. That
  is the single strongest argument for this page existing.
- **Two naming conventions coexist, deliberately.** The TorQ-facing
  variables have no separators (`TORQDATA`, `KDBLOG`, `UQFROOT`) because they
  are TorQ's own convention and the vendored tree reads them by those names.
  Everything this repository introduced uses `UQF_` with underscores. Neither
  is wrong; picking one and renaming the other would mean editing
  `lib/torq`, which H-01 forbids.

## Read by this repository

| Variable | Read by | Required | Unset behaviour |
|---|---|---|---|
| `UQF_DRY_RUN` | `.qwrt.is_dry_run` (`src/etl/core/worker_runtime.q`) | no | false — the worker publishes for real. Opt-in on purpose: defaulting to true would make a worker silently do nothing and report success |
| `UQF_BACKFILL_WORKER` | `scripts/torq_backfill.q` | yes, for a backfill process | the process refuses to start and names every missing variable at once |
| `UQF_BACKFILL_VERSION` | as above | yes | as above — a run that cannot name its source release cannot record coverage (ETL-09) |
| `UQF_BACKFILL_FROM` | as above | yes | as above. Deliberately no default: a backfill that guessed a range would publish the wrong window and record it as covered |
| `UQF_BACKFILL_TO` | as above | yes | as above |
| `UQF_SOURCE_CRED_<SOURCE>` | `.qsrc.require_credentials` (`src/etl/core/source_contract.q`) | per live source | `require_credentials` refuses and names the variable. There is deliberately no file and no vault fallback (ETL-07) |
| `UQFROOT` | `scripts/torq_*.q`, `wizard.py`'s generated q | yes | the `\l` of every repository script fails. Set by `build_env`, not by hand |
| `UQFSTATUSDIR` | `.qpipe.status_dir` (`scripts/torq_pipeline.q`) | no | falls back to `$TORQDATA/status`. Pairs with `UQF_FRONTEND_STATUS_DIR` on the reading side |
| `DATABENTO_DATA_DIR` | `.qdata.databentoDir` (`src/integrations/data.q`) | for that path only | `.qdata.cfg` also accepts it from a `.env` file, then throws naming the key. Note this is a *data directory*, not a credential — ETL-07's no-file rule is about secrets |
| `UQF_FRONTEND_GATEWAY_HOST` | `uqf_frontend.config.Settings.from_env` | no | `localhost` |
| `UQF_FRONTEND_GATEWAY_PORT` | as above | no | `6052` |
| `UQF_FRONTEND_GATEWAY_USER` | as above | no | empty |
| `UQF_FRONTEND_GATEWAY_PASSWD` | as above | no | empty |
| `UQF_FRONTEND_TIMEOUT` | as above | no | `30` seconds, enforced per query by kola (FE-11) |
| `UQF_FRONTEND_MAX_ROWS` | as above | no | `DEFAULT_MAX_ROWS` — a hard cap, whatever the caller asks for |
| `UQF_FRONTEND_BASE_PORT` | as above | no | `6050`. Must match the port the stack was started with, or every probe targets the wrong process |
| `UQF_FRONTEND_PROCESS_CSV` | as above | no | fleet health reports itself *unconfigured* rather than returning an empty fleet (FE-01) |
| `UQF_FRONTEND_STATUS_DIR` | as above | no | the backfill view reports itself unconfigured rather than returning an empty list, which would look identical to an idle fleet (FE-06) |
| `UQF_FRONTEND_PROCESSES` | as above | no | the per-process query log reports nothing configured rather than an empty log (FE-04) |
| `UQF_FRONTEND_WEB_DIST` | as above | no | no built React app is served under `/ui/`; the API still serves |
| `UQF_API_ORIGIN` | `web/vite.config.ts` | no | `http://127.0.0.1:8000`. Dev proxy only — it has no effect on a built bundle |
| `LOG_LEVEL` | `torq_orchestrator.logger.decorators` | no | the logger's own default |
| `LOG_REGEX` | `torq_orchestrator.logger.core` | no | no name filtering |
| `CRYPTORUST_ROOT` | `torq_orchestrator.crypto` | no | the checkout is located by the search path in `cryptorust_root`'s docstring |
| `UQF_SMOKE_TARGETS` | `tests/q/smoke_external_metadata.q` | yes, for that script | the smoke check has nothing to connect to and says so |
| `UQF_SMOKE_TABLES` | as above | yes, for that script | as above |
| `UQF_SMOKE_TIMEOUT_MS` | as above | no | `5000` |

`UQF_SMOKE_*` sit in `tests/q/` but are listed here because that script is an
operator tool — it is run by hand against a real source, which is exactly why
it is not part of the unit lane. Variables that only a fixture ever sets
(`UQF_TEST_CFG_KEY`, `UQF_SHARED`, `UQFQ` and the `.qwcfg` precedence
fixtures) are deliberately absent: they are not an operator's business, and
listing them would bury the twenty-three above that are.

## Produced by the orchestrator — do not set these by hand

`torq_orchestrator.env.build_env` computes these from `UqfStackPaths` and
hands them to `torq.sh`; `process.csv`'s `${VAR}` and `{VAR}+N` placeholders
resolve against the same dict. Setting one in your shell does not override
anything — `build_env` wins — but it will make `uqf-stack list` and the
running stack disagree about where data lives, which is a confusing way to
spend an afternoon.

| Variable | Value |
|---|---|
| `TORQHOME` | the vendored TorQ tree |
| `TORQAPPHOME` | the starter-pack app tree |
| `TORQDATA` | data root; `UQFSTATUSDIR` falls back inside it |
| `TORQPROCESSES` | the generated `process.csv` |
| `UQFSCRIPTS` | `scripts/` |
| `KDBCONFIG` | `$TORQHOME/config` |
| `KDBCODE` | `$TORQHOME/code` |
| `KDBAPPCONFIG` | `$TORQAPPHOME/appconfig` |
| `KDBAPPCODE` | `$TORQAPPHOME/code` |
| `KDBLIB` | `$TORQHOME/lib` |
| `KDBTESTS` | `$TORQHOME/tests` |
| `KDBLOG` | `$TORQDATA/logs` |
| `KDBHDB` | `$TORQDATA/hdb` |
| `KDBWDB` | `$TORQDATA/wdbhdb` |
| `KDBTPLOG` | `$TORQDATA/tplogs` |
| `KDBDQCDB` | the DQC database |
| `KDBDQEDB` | the DQE database |
| `KDBBASEPORT` | the port block base |
| `KDBSTACKID` | `-stackid <base port>` |
| `RLWRAP` | `rlwrap` |
| `QCON` | `qcon` |
| `QCMD` | `q` |

## Prerequisites, not configuration

`QHOME` must point at a real KDB-X installation (`~/.kx` on this machine).
This tree targets KDB-X alone; there is no fallback interpreter. `HOME` is read only to locate that default.

## How this page is kept honest

`scripts/check_env_reference.py --check` runs in CI and fails the build when
the code and this page disagree, in either direction:

- a variable read by `src/`, `scripts/`, a package's `src/`, `web/` or the
  smoke script and **not** listed here;
- a variable listed here that nothing reads any more, which is how a
  reference page ends up describing a previous version of the system.

Pattern rows (`UQF_SOURCE_CRED_<SOURCE>`) are exempt from the second
direction only — a mechanically built name has no literal to find.
