# Logs

Read what the stack's processes logged, filter it by level and subsystem, and
find where a process stopped.

## Reading logs

Every process writes `out_<procname>.log` and `err_<procname>.log` in
`output/uqs/logs/`. Check these first when a process shows `down`. `uqs logs`
reads them and sorts the lines by their own timestamps. `-f` keeps following
across restarts and the daily roll. `--level` keeps that level and above.

```bash
uqs logs                          # last 20 lines per process, all processes
uqs logs stp1 rdb1 -l 50          # last 50 lines each, merged and time-sorted
uqs logs -f                       # the last 20 lines, then live, Ctrl-C to stop
uqs logs fxorderbookfeed1 -f --level WARNING   # live tail, warnings/errors only
uqs logs --multitail rdb1 fxpositions1          # a pane for each out_/err_ log, stacked
```

`--multitail` follows the same files in multitail, one pane per file. It needs
the `multitail` binary (`brew install multitail`, `apt install multitail`).

`uqs start -f` and `uqs restart -f` start what they would without it, then
stream those processes' logs until Ctrl-C. The processes keep running. With
`--stop-on-exit`, Ctrl-C also stops what the command started. `uqs job backfill`
and `uqs job attachment add` take the same flags.

An attached backfill or receiver has no `out_` log. Read its latest run with
`uqs logs --attachment NAME --worker WORKER`, described in
[attach-existing-torq](attach-existing-torq.md#seeing-what-happened).

### Channels: which subsystem a line concerns

A line's channel is the subsystem it concerns. In a log line,
`time|host|proctype|proc|level|id|message`, `proc` is the process that wrote it
and `id` is the channel.

  | Channel     | Scope                                                          |
  | ---         | ---                                                            |
  | `lifecycle` | process and job startup, shutdown, run and window completion   |
  | `ipc`       | connections, requests, responses, timeouts, retries            |
  | `source`    | source readiness, fetching, queries                            |
  | `transform` | normalisation, matching, calculations, rejected rows           |
  | `storage`   | writes, persistence, sorting, compression                      |
  | `coverage`  | coverage updates, checkpoints, replay decisions                |
  | `eod`       | rollover, handover, reload coordination                        |
  | `config`    | configuration, file loading, declarations                      |

`uqs logs` shows the channel beside the process. `--channel NAME` keeps only
that subsystem's lines. It repeats, and it combines with process names,
`--level` and `-f`.

```bash
uqs logs rdb1 -f --channel source --channel ipc --level DEBUG
uqs logs --json --channel coverage                   # each line carries "channel"
```

**`--channel` filters what you view. It does not stop a record being generated
or rendered:** whether a line is written at all is its level's decision
(`.qetl.log.debug[1b]`, `.qetl.log.trace[1b]`), and a hidden channel's lines are
still produced, still in the file and still published where the level is
published.

A line whose `id` is not one of the eight channels, such as one TorQ's own code
wrote (`main`, `dotz`), is shown as it is. With `--channel`, it is left out.

### Subscribing to published lines

`--subscribe` prints the lines the processes publish, live until Ctrl-C. It
takes process names, `--channel` and a minimum `--level`.

```bash
uqs logs all --subscribe --level ERROR                  # every error, from every process
uqs logs fxpositions1 --subscribe --channel source      # one process's source warnings and errors
```

Processes publish WARNING and ERROR by default, and nothing below (`.lg.pubmap`
in `scripts/torqconfig/settings/default.q`). Each subscribed process holds one
more connection, so near the licence connection limit, name the processes you
need. Use `-f` for a process that is down or is not a TorQ process.

## Where a process stopped

Every uqf process logs the stages where it can stall, so the last line of
`uqs logs <procname>` says where it stopped.

  | Last line                                                                   | What it means                                                                                                                                                                                                                                                                                                   |
  | ---                                                                         | ---                                                                                                                                                                                                                                                                                                             |
  | `qtorq: loading uqf tree from ...` with no `loaded in` after it             | A q file failed to load. The error follows, or is in `err_<procname>.log`.                                                                                                                                                                                                                                      |
  | `starting streaming job`                                                    | The job, and what it subscribes to and publishes. Logged before anything can block.                                                                                                                                                                                                                             |
  | `waiting for the tickerplant - if this is the last line, it is not running` | `stp1` is down. Run `uqs start stp1`.                                                                                                                                                                                                                                                                           |
  | `subscribing` / `streaming job wired - running`                             | Subscribed and running.                                                                                                                                                                                                                                                                                         |
  | no `first batch received` for a table                                       | Nothing arrives on it. Its publisher is down or publishes nothing.                                                                                                                                                                                                                                              |
  | `first batch received` but no `first rows published`                        | Input arrives and the job publishes nothing from it.                                                                                                                                                                                                                                                            |
  | `on_batch failed`                                                           | The job's handler threw. The line gives the table and the error.                                                                                                                                                                                                                                                |
  | `has no credential - refusing to write its fixture` (init)                  | The backfill's source has no credential. Set it to run live, or pass `--fixture` (`UQF_FIXTURE_WRITES=1`) for a demo run, whose coverage is recorded under `<version>~fixture`.                                                                                                                                 |
  | `no credential - running on the source's fixture` (WARNING)                 | The backfill publishes the fixture in place of live data. The line gives the `sources.csv` row to add and the variable that overrides it (`UQF_SOURCE_CRED_<SOURCE>`). Add the row (`uqs config sources stub SOURCE` starts one), or export the variable in the shell you run `uqs job backfill` from.          |
  | `hdb reload: could not open a handle to every registered hdb` (ERROR)       | The backfill wrote its rows and the running HDB has not reloaded them. TorQ's `connection to ... failed: access` line just before says the login was refused. A backfill connects as the ETL identity (`appconfig/passwords/metrics.txt`) unless `passwords/backfill.txt` or `<procname>.txt` gives it its own. |
  | `backfill finished with rows a running hdb has not reloaded` (ERROR)        | The run's last reload did not reach every registered HDB. The rows are on disk and covered. A running HDB serves them once it reloads or restarts.                                                                                                                                                              |
  | `idle - every window in the range is already covered`                       | Nothing to do at this `--source-version`. A new source release is a new version.                                                                                                                                                                                                                                |
  | `checkpoint is for another run - starting from the beginning`               | The range or version changed since the last run, so its checkpoint does not apply.                                                                                                                                                                                                                              |
  | `state file unreadable - using the previous generation` (WARNING)           | A cursor, checkpoint or ledger file did not parse and its `.bak` was used. At most one page or window is done again.                                                                                                                                                                                            |
  | `load_checkpoint: ... is unreadable ... and so is its .bak`                 | Neither generation of the backfill's checkpoint parses. `uqs job checkpoint remove WORKER` starts the run over. Windows already in the coverage ledger are still skipped.                                                                                                                                       |

### Debug and trace

The DEBUG level adds the process's pid, port and cwd, its subscriptions and
timers, and every batch and publish. A backfill adds its stage timings, coverage
gaps, and each fetch, retry, lock and checkpoint. `--trace` goes one level lower
and adds every query the source is sent: the SQL statement, or the q lambda.

```bash
uqs --debug job backfill <worker> ...                 # a backfill: passes -verbose
uqs exec -- start <procname> -extras -verbose         # any process, at start
uqs query ".qetl.log.debug 1b" --port <port>              # a process already running, no restart
uqs job backfill <worker> ... --trace       # a backfill: passes -trace
uqs start <procname> --trace                # live jobs, at start (also restart)
uqs query <procname> ".qetl.log.trace 1b"   # a process already running, no restart
```

`-verbose` is uqf's own flag. TorQ's `-debug` is a different flag, which also
stops the log going to its file. `uqs start --trace` cannot reach a process that
is already up, and names each one it skipped. Trace those with
`uqs restart <procname> --trace` or the `uqs query` above. TorQ's own processes
(rdb, tickerplant) ignore `-trace`. `uqs logs --level DEBUG` hides trace lines.

Every line logged while a window runs carries its `worker`, `run` and
`range_from`/`range_to`, so you can grep one window's lines together. Request
traces add a `request` number. It restarts at 1 in every process, so across
processes identify a request by its log file and its `request` together.

## CLI's own logging

`logs --level` filters what the q processes wrote. To see what `uqs` itself is
doing, put `--debug` before the command (`uqs --debug status`), or set
`LOG_LEVEL=DEBUG` for a shell session. On `status`, `--debug` also says why a
column is blank and prints each process's load time on its latest start.
`NO_COLOR=1` turns colour off, and `FORCE_COLOR=1` keeps it through a pipe.

## The frontend's activity history

`uqs activity list` and `uqs activity export` read the frontend's history of
what operators did in the browser, and its audit of every request that changed
something. They need no API running. See the [frontend
README](../../python/uqf_frontend/README.md#activity-history-and-the-action-audit).
