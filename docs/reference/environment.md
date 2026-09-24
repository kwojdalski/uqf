# Environment variables

The one operator-facing list. Hand-written, because "required or optional",
"what happens when it is unset" and "who sets it" are judgements no generator
can read off the code — and machine-checked for completeness by
`scripts/gates/check_env_reference.py`, so it cannot quietly fall behind.

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
  `lib/torq`, which the vendored-tree rule forbids.

- **Almost none of this is read from `.env`.** Exactly one reader consults
  that file: `.qdata.cfg` in `src/integrations/data.q`, for
  `DATABENTO_DATA_DIR`. Every Python package, every TorQ script and the
  browser app read the OS environment only, so a variable placed in `.env`
  for any of them is not set, it is ignored — and the code reports it
  missing while the file plainly contains it. `.env.example` lists what
  belongs there and sorts the rest by theme (secrets, per-run arguments,
  deployment wiring, developer knobs); `check_env_reference.py` refuses a
  key in it that nothing reads from `.env`.

## Read by this repository

| Variable | Read by | Required | Unset behaviour |
|---|---|---|---|
| `UQF_DRY_RUN` | `.qwrt.is_dry_run` (`src/etl/core/worker_runtime.q`) | no | false — the worker publishes for real. Opt-in on purpose: defaulting to true would make a worker silently do nothing and report success |
| `UQF_STREAM_JOB` | `scripts/processes/torq_stream.q` | no | the process falls back to its own procname, which is how the stack starts it; set this only to run a job by hand |
| `UQF_BACKFILL_WORKER` | `scripts/processes/torq_backfill.q` | yes, for a backfill process | the process refuses to start and names every missing variable at once |
| `UQF_BACKFILL_VERSION` | as above | yes | as above — a run that cannot name its source release cannot record coverage (ETL-09) |
| `UQF_BACKFILL_FROM` | as above | yes | as above. Deliberately no default: a backfill that guessed a range would publish the wrong window and record it as covered |
| `UQF_BACKFILL_TO` | as above | yes | as above |
| `UQF_SOURCE_CRED_<SOURCE>` | `.qsrc.require_credentials` (`src/etl/core/source_contract.q`) | per live source | `require_credentials` refuses and names the variable. There is deliberately no file and no vault fallback (ETL-07) |
| `Q` | `scripts/test.py` | no | `~/.kx/bin/q`. The interpreter every q lane runs. Together with `QHOME` this is the deliberate, explicit way to point the suite at another q — there is no automatic fallback, see [the README](../../README.md#requirements). It was invisible to this list until the runner became Python: `check_env_reference.py` reads `.py` and `.q`, never `.sh` |
| `QLINTER` | `scripts/gates/check_q_traps.py` | no | `qlinter` on `PATH`. The [q linter](https://github.com/kwojdalski/q-lint), which the trap hook delegates thirteen of its fifteen rules to. Set it to point at a build that is not installed — the same escape hatch `Q` gives for the interpreter. The hook refuses rather than skipping when neither finds it: a hook that reports success over checks that did not run is worse than one that fails |
| `UQFROOT` | `scripts/torq_*.q` | yes | the `\l` of every repository script fails. Set by `build_env`, not by hand |
| `UQFSTATUSDIR` | `.qstatus.status_dir` (`src/etl/core/status.q`) | no | falls back to `$TORQDATA/status`. Pairs with `UQF_FRONTEND_STATUS_DIR` on the reading side |
| `DATABENTO_DATA_DIR` | `.qdata.databentoDir` (`src/integrations/data.q`) | for that path only | `.qdata.cfg` also accepts it from a `.env` file, then throws naming the key. Note this is a *data directory*, not a credential — ETL-07's no-file rule is about secrets |
| `UQF_FRONTEND_GATEWAY_HOST` | `uqf_frontend.config.Settings.from_env` | no | `localhost` |
| `UQF_FRONTEND_GATEWAY_PORT` | as above | no | `6057` — the base port +7, where `process.csv` puts `gateway1`; derived from `UQF_FRONTEND_BASE_PORT` when that is set |
| `UQF_FRONTEND_GATEWAY_USER` | as above | no | empty |
| `UQF_FRONTEND_GATEWAY_PASSWD` | as above | no | empty |
| `UQF_FRONTEND_TIMEOUT` | as above | no | `30` seconds, enforced per query by kola (FE-11) |
| `UQF_FRONTEND_ENABLE_WRITES` | as above | no | **false — every `/control/*` route refuses with 403.** Off by default as the security posture, not caution: FE-15 ships one shared credential and FE-20's identity is *claimed* through a header anyone can set, which is defensible while every route is a read. Once a route can stop the fleet or rewrite `process.csv`, "anyone who can reach the port" is the whole access control. Accepts true/false/1/0/yes/no/on/off; anything else refuses to start rather than reading as false |
| `UQF_FRONTEND_STACK_ROOT` | as above | no | the repository the orchestrator package was installed from. Only the `/control/*` routes use it, and a path that is not a checkout (no `lib/torq/torq.sh`) is refused rather than acted on — starting the wrong stack is worse than not starting one |
| `UQF_FRONTEND_CAPTURE_DIR` | as above | no | where usage rows are captured to, as JSON Lines (FE-13). **Unset means capture does not run**, and that is lossy rather than merely inactive: `.usage.flushtime` is one day in a standard stack, so without it the usage view can only ever show the last day and older history is gone for good |
| `UQF_FRONTEND_CAPTURE_INTERVAL` | `900` | no | seconds between capture sweeps. Keep it well under the flush window — the interval is the whole guarantee, and one longer than the window loses rows silently |
| `UQF_FRONTEND_MAX_ROWS` | as above | no | `DEFAULT_MAX_ROWS` — a hard cap, whatever the caller asks for |
| `UQF_FRONTEND_BASE_PORT` | as above | no | `6050`. Must match the port the stack was started with, or every probe targets the wrong process |
| `UQF_FRONTEND_PROCESS_CSV` | as above | no | fleet health reports itself *unconfigured* rather than returning an empty fleet (FE-01) |
| `UQF_FRONTEND_STATUS_DIR` | as above | no | the backfill view reports itself unconfigured rather than returning an empty list, which would look identical to an idle fleet (FE-06) |
| `UQF_FRONTEND_PROCESSES` | as above | no | the per-process query log reports nothing configured rather than an empty log (FE-04) |
| `UQF_FRONTEND_WEB_DIST` | as above | no | no built React app is served under `/ui/`; the API still serves |
| `UQF_API_ORIGIN` | `web/vite.config.ts` | no | `http://127.0.0.1:8000`. Dev proxy only — it has no effect on a built bundle |
| `LOG_LEVEL` | `uqs.cli.entry` (`_env_log_level`), `uqs.logger.decorators` | no | `INFO`. Sets the level every `uqs` command logs at, and turns on the `logged_function` call trace at `DEBUG`. An unrecognised value falls back to `INFO` rather than aborting — a typo in a log level must not stop the fleet being started or inspected. `uqs --debug` is the same thing per-invocation, and wins over this |
| `LOG_REGEX` | `uqs.logger.core` | no | no name filtering |
| `NO_COLOR` | `uqs.logger.core` (`_colorize`) | no | colour on a terminal, plain in a pipe or a file. Set to anything non-empty and `uqs`'s own log lines are never coloured; wins over `FORCE_COLOR` ([no-color.org](https://no-color.org)) |
| `FORCE_COLOR` | as above | no | as above. Set to anything non-empty and `uqs`'s log lines are coloured even when piped - for a pager that renders colour, e.g. `FORCE_COLOR=1 uqs logs \| less -R` |
| `DATABENTO_API_KEY` | `uqs.external.databento_feed` | for `uqs databento start` only | Databento's own variable name, so an existing export works unchanged. The live feed refuses to start without it rather than failing on its first call; the ODBC backfill does not read it |
| `CRYPTORUST_ROOT` | `uqs.external.crypto` | no | the checkout is located by the search path in `cryptorust_root`'s docstring |
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

`uqs.stack.env.build_env` computes these from `UqsPaths` and
hands them to `torq.sh`; `process.csv`'s `${VAR}` and `{VAR}+N` placeholders
resolve against the same dict. Setting one in your shell does not override
anything — `build_env` wins — but it will make `uqs list` and the
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
| `RLWRAP` | `rlwrap` |
| `QCON` | `qcon` |
| `QCMD` | `q` |

## Prerequisites, not configuration

`QHOME` must point at a real q installation (`~/.kx` for KDB-X, the preferred
and only verified interpreter — see the [README](../../README.md#requirements)
for what else may work). Nothing falls back automatically: if `QHOME` and `$Q`
point somewhere the tree is not verified against, that is the operator's
deliberate choice, not a default. `HOME` is read only to locate `~/.kx`.

## How this page is kept honest

`scripts/gates/check_env_reference.py --check` runs in CI and fails the build when
the code and this page disagree, in either direction:

- a variable read by `src/`, `scripts/`, a package's `src/`, `web/` or the
  smoke script and **not** listed here;
- a variable listed here that nothing reads any more, which is how a
  reference page ends up describing a previous version of the system.

Pattern rows (`UQF_SOURCE_CRED_<SOURCE>`) are exempt from the second
direction only — a mechanically built name has no literal to find.
